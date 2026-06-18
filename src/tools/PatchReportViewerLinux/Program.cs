using Mono.Cecil;
using Mono.Cecil.Cil;
using System;
using System.IO;
using System.Linq;

if (args.Length < 1)
{
    Console.Error.WriteLine("Usage: PatchReportViewerLinux <Microsoft.ReportViewer.Common.dll> [output.dll]");
    return 1;
}

string inputPath = args[0];
string outputPath = args.Length > 1 ? args[1] : inputPath;
bool replaceInput = Path.GetFullPath(inputPath) == Path.GetFullPath(outputPath);
string writePath = replaceInput ? inputPath + ".linux-patched.tmp" : outputPath;

if (!File.Exists(inputPath))
{
    Console.Error.WriteLine($"ERROR: {inputPath} not found");
    return 1;
}

var resolver = new DefaultAssemblyResolver();
resolver.AddSearchDirectory(Path.GetDirectoryName(inputPath) ?? ".");
if (Directory.Exists("/bc/service"))
    resolver.AddSearchDirectory("/bc/service");
if (Directory.Exists("/bc/service/SideServices"))
    resolver.AddSearchDirectory("/bc/service/SideServices");

var readerParameters = new ReaderParameters
{
    AssemblyResolver = resolver,
    ReadingMode = ReadingMode.Immediate,
};

using var assembly = AssemblyDefinition.ReadAssembly(inputPath, readerParameters);
var module = assembly.MainModule;

var type = module.GetType("Microsoft.ReportingServices.Diagnostics.RevertImpersonationContext");
if (type == null)
{
    Console.Error.WriteLine("ERROR: RevertImpersonationContext not found");
    return 1;
}

bool patched = false;
patched |= PatchCallbackWrapper(module, type, "Run");
patched |= PatchCallbackWrapper(module, type, "RunFromRestrictedCasContext");
patched |= PatchExpressionHostCompilerCleanup(module);
patched |= PatchGenericSansSerifFallback(module);

if (!patched)
{
    Console.Error.WriteLine("ERROR: no ReportViewer impersonation methods were patched");
    return 1;
}

if (File.Exists(writePath))
    File.Delete(writePath);

assembly.Write(writePath);
if (replaceInput)
{
    File.Move(writePath, inputPath, overwrite: true);
}

Console.WriteLine($"Patched ReportViewer rendering compatibility paths in {outputPath}");
return 0;

static bool PatchCallbackWrapper(ModuleDefinition module, TypeDefinition type, string methodName)
{
    var method = type.Methods.SingleOrDefault(m =>
        m.Name == methodName &&
        m.Parameters.Count == 1 &&
        m.Parameters[0].ParameterType.FullName == "Microsoft.ReportingServices.Diagnostics.ContextBody");

    if (method == null)
        throw new InvalidOperationException($"{type.FullName}.{methodName}(ContextBody) not found");

    if (!method.HasBody)
        throw new InvalidOperationException($"{type.FullName}.{methodName} has no body");

    var contextBodyType = module.GetType("Microsoft.ReportingServices.Diagnostics.ContextBody")
        ?? throw new InvalidOperationException("ContextBody type not found");
    var invoke = contextBodyType.Methods.Single(m => m.Name == "Invoke" && m.Parameters.Count == 0);
    var invokeReference = module.ImportReference(invoke);

    method.Body.Variables.Clear();
    method.Body.ExceptionHandlers.Clear();
    method.Body.Instructions.Clear();
    method.Body.InitLocals = false;

    var il = method.Body.GetILProcessor();
    il.Append(il.Create(OpCodes.Ldarg_0));
    il.Append(il.Create(OpCodes.Callvirt, invokeReference));
    il.Append(il.Create(OpCodes.Ret));

    Console.WriteLine($"Patched {type.FullName}.{methodName}: callback -> ContextBody::Invoke");
    return true;
}

static bool PatchExpressionHostCompilerCleanup(ModuleDefinition module)
{
    var type = module.GetType("Microsoft.ReportingServices.RdlExpressions.ExprHostCompiler");
    if (type == null)
        throw new InvalidOperationException("Microsoft.ReportingServices.RdlExpressions.ExprHostCompiler not found");

    var method = type.Methods.SingleOrDefault(m => m.Name == "InternalCompile");
    if (method == null || !method.HasBody)
        throw new InvalidOperationException("ExprHostCompiler.InternalCompile not found");

    int patched = 0;
    var il = method.Body.GetILProcessor();
    foreach (var instruction in method.Body.Instructions)
    {
        if (instruction.OpCode == OpCodes.Call &&
            instruction.Operand is MethodReference methodReference &&
            methodReference.FullName == "System.Void System.IO.File::Delete(System.String)")
        {
            instruction.OpCode = OpCodes.Pop;
            instruction.Operand = null;
            patched++;
        }
    }

    if (patched == 0)
    {
        Console.WriteLine($"{type.FullName}.{method.Name}: System.IO.File::Delete already absent");
        return false;
    }

    Console.WriteLine($"Patched {type.FullName}.{method.Name}: System.IO.File::Delete -> pop");
    return true;
}

static bool PatchGenericSansSerifFallback(ModuleDefinition module)
{
    var type = module.GetType("Microsoft.ReportingServices.Rendering.RichText.FontCache");
    if (type == null)
        throw new InvalidOperationException("Microsoft.ReportingServices.Rendering.RichText.FontCache not found");

    var method = type.Methods.SingleOrDefault(m => m.Name == "CreateGdiPlusFont");
    if (method == null || !method.HasBody)
        throw new InvalidOperationException("FontCache.CreateGdiPlusFont not found");

    bool replacedGenericFamily = false;
    bool replacedConstructor = false;
    var instructions = method.Body.Instructions;

    for (int i = 0; i < instructions.Count; i++)
    {
        var instruction = instructions[i];
        if (instruction.OpCode == OpCodes.Call &&
            instruction.Operand is MethodReference methodReference &&
            methodReference.FullName == "System.Drawing.FontFamily System.Drawing.FontFamily::get_GenericSansSerif()")
        {
            instruction.OpCode = OpCodes.Ldstr;
            instruction.Operand = "DejaVu Sans";
            replacedGenericFamily = true;

            for (int j = i + 1; j < instructions.Count; j++)
            {
                if (instructions[j].OpCode == OpCodes.Newobj &&
                    instructions[j].Operand is MethodReference constructorReference &&
                    constructorReference.DeclaringType.FullName == "System.Drawing.Font" &&
                    constructorReference.Parameters.Count == 3 &&
                    constructorReference.Parameters[0].ParameterType.FullName == "System.Drawing.FontFamily")
                {
                    var fontStringConstructor = new MethodReference(
                        ".ctor",
                        module.TypeSystem.Void,
                        constructorReference.DeclaringType)
                    {
                        HasThis = true,
                    };
                    fontStringConstructor.Parameters.Add(new ParameterDefinition(module.TypeSystem.String));
                    fontStringConstructor.Parameters.Add(new ParameterDefinition(module.TypeSystem.Single));
                    fontStringConstructor.Parameters.Add(new ParameterDefinition(constructorReference.Parameters[2].ParameterType));
                    instructions[j].Operand = fontStringConstructor;
                    replacedConstructor = true;
                    break;
                }
            }

            break;
        }
    }

    if (!replacedGenericFamily || !replacedConstructor)
    {
        Console.WriteLine($"{type.FullName}.{method.Name}: GenericSansSerif fallback already absent");
        return false;
    }

    Console.WriteLine($"Patched {type.FullName}.{method.Name}: GenericSansSerif -> DejaVu Sans");
    return true;
}
