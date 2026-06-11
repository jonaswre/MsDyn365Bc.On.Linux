using Mono.Cecil;
using Mono.Cecil.Cil;
using System;
using System.IO;
using System.Linq;

/// <summary>
/// Patches Nav.Ncl.dll so deleting the current NavUserPassword user follows the
/// standard container test surface.
///
/// BC table trigger SystemTableTriggers.OnBeforeDeleteAsync checks table
/// 2000000073 (Active Session) before deleting table 2000000120 (User) and
/// throws NavNCLUserTableUserCannotBeDeletedAlreadyLoggedInException when the
/// user has an active session. Standard Microsoft test cleanup expects to be
/// able to delete the ADMIN user during setup/teardown without ending the test
/// session. This patch skips only that active-session throw and then continues
/// with the remaining user-delete validation.
/// </summary>
static class PatchUserDeleteGuard
{
    public static int Run(string inputPath, string outputPath)
    {
        if (!File.Exists(inputPath))
        {
            Console.WriteLine($"ERROR: {inputPath} not found");
            return 1;
        }

        try
        {
            var resolver = new DefaultAssemblyResolver();
            resolver.AddSearchDirectory(Path.GetDirectoryName(inputPath) ?? ".");
            if (Directory.Exists("/bc/service"))
                resolver.AddSearchDirectory("/bc/service");

            var readerParams = new ReaderParameters
            {
                AssemblyResolver = resolver,
                ReadWrite = inputPath == outputPath,
            };
            using var assembly = AssemblyDefinition.ReadAssembly(inputPath, readerParams);
            var module = assembly.MainModule;

            MethodDefinition? moveNext = null;
            Instruction? throwFactory = null;
            foreach (var candidate in module.GetTypes()
                         .Where(t => t.DeclaringType?.FullName == "Microsoft.Dynamics.Nav.Runtime.SystemTableTriggers")
                         .SelectMany(t => t.Methods)
                         .Where(m => m.Name == "MoveNext" && m.Body != null))
            {
                var match = candidate.Body.Instructions.FirstOrDefault(IsUserAlreadyLoggedInExceptionFactory);
                if (match != null)
                {
                    moveNext = candidate;
                    throwFactory = match;
                    break;
                }
            }

            if (moveNext == null || throwFactory == null)
            {
                Console.WriteLine("ERROR: active-session user delete exception factory not found");
                return 1;
            }

            Console.WriteLine($"Found {moveNext.DeclaringType.FullName}.{moveNext.Name}");
            var instructions = moveNext.Body.Instructions;
            var factoryIndex = instructions.IndexOf(throwFactory);
            var branch = instructions
                .Take(factoryIndex)
                .Reverse()
                .Take(40)
                .FirstOrDefault(i => i.OpCode == OpCodes.Brfalse || i.OpCode == OpCodes.Brfalse_S);

            if (branch == null)
            {
                if (instructions
                    .Take(factoryIndex)
                    .Reverse()
                    .Take(40)
                    .Any(i =>
                    {
                        var next = i.Next;
                        return i.OpCode == OpCodes.Pop
                            && next != null
                            && (next.OpCode == OpCodes.Br || next.OpCode == OpCodes.Br_S);
                    }))
                {
                    Console.WriteLine("Already patched: active-session user delete guard is skipped");
                    return 0;
                }

                Console.WriteLine("ERROR: branch guarding active-session user delete exception not found");
                return 1;
            }

            if (branch.Operand is not Instruction continueAt)
            {
                Console.WriteLine("ERROR: active-session branch target is not an instruction");
                return 1;
            }

            var il = moveNext.Body.GetILProcessor();
            var skipThrow = il.Create(OpCodes.Br, continueAt);
            branch.OpCode = OpCodes.Pop;
            branch.Operand = null;
            il.InsertAfter(branch, skipThrow);

            if (inputPath == outputPath)
                assembly.Write();
            else
                assembly.Write(outputPath);

            Console.WriteLine("Patched Nav.Ncl.dll active-session user delete guard");
            return 0;
        }
        catch (Exception ex)
        {
            Console.WriteLine($"ERROR: {ex.Message}");
            Console.WriteLine(ex.StackTrace);
            return 1;
        }
    }

    private static bool IsUserAlreadyLoggedInExceptionFactory(Instruction instruction)
    {
        return instruction.OpCode == OpCodes.Call
            && instruction.Operand is MethodReference method
            && method.DeclaringType.FullName
                == "Microsoft.Dynamics.Nav.Types.Exceptions.NavNCLUserTableUserCannotBeDeletedAlreadyLoggedInException"
            && method.Name == "Create";
    }
}
