// Stub for Microsoft.AspNetCore.Server.HttpSys — redirects to Kestrel on Linux.
// BC calls: builder.UseHttpSys(configure) then builder.UseUrls("http://+:7048/BC/api")
// We redirect to Kestrel and use an IStartupFilter to strip URL paths at app startup.
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Net.WebSockets;
using System.Security.Claims;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;

namespace Microsoft.AspNetCore.Server.HttpSys
{
    public class HttpSysOptions
    {
        public bool AllowSynchronousIO { get; set; }
        public long? MaxConnections { get; set; }
        public int MaxAccepts { get; set; }
        public long? MaxRequestBodySize { get; set; }
        public AuthenticationManager Authentication { get; } = new AuthenticationManager();
        public TimeoutManager Timeouts { get; } = new TimeoutManager();
        public bool ThrowWriteExceptions { get; set; }
        public int RequestQueueLimit { get; set; }
        public string? RequestQueueName { get; set; }
        public bool UnsafePreferInlineScheduling { get; set; }
    }

    public class AuthenticationManager
    {
        public bool AllowAnonymous { get; set; } = true;
        public AuthenticationSchemes Schemes { get; set; } = AuthenticationSchemes.None;
        public bool AutomaticAuthentication { get; set; }
    }

    public class TimeoutManager
    {
        public TimeSpan IdleConnection { get; set; }
        public TimeSpan EntityBody { get; set; }
        public TimeSpan DrainEntityBody { get; set; }
        public TimeSpan RequestQueue { get; set; }
        public TimeSpan HeaderWait { get; set; }
        public long MinSendBytesPerSecond { get; set; }
    }

    [Flags]
    public enum AuthenticationSchemes
    {
        None = 0, Basic = 1, Anonymous = 2, NTLM = 4, Negotiate = 8, Kerberos = 16,
    }
}

namespace Microsoft.AspNetCore.Hosting
{
    public static class WebHostBuilderHttpSysExtensions
    {
        private static readonly System.Collections.Generic.HashSet<int> _boundPorts = new();

        public static IWebHostBuilder UseHttpSys(this IWebHostBuilder builder,
            Action<Microsoft.AspNetCore.Server.HttpSys.HttpSysOptions> configure)
        {
            var opts = new Microsoft.AspNetCore.Server.HttpSys.HttpSysOptions();
            configure?.Invoke(opts);

            builder.UseKestrel(k =>
            {
                k.AllowSynchronousIO = opts.AllowSynchronousIO;
                if (opts.MaxRequestBodySize.HasValue)
                    k.Limits.MaxRequestBodySize = opts.MaxRequestBodySize;
            });

            builder.ConfigureServices(services =>
            {
                services.AddSingleton<IStartupFilter>(new UrlStrippingStartupFilter(_boundPorts));
            });

            return builder;
        }

        public static IWebHostBuilder UseHttpSys(this IWebHostBuilder builder)
        {
            return builder.UseKestrel();
        }
    }

    /// <summary>
    /// Startup filter that:
    /// 1. Strips URL paths from server addresses (Kestrel can't handle path-based URLs)
    /// 2. Adds UsePathBase() so BC's middleware routes correctly
    /// 3. Enforces the configured Basic credentials before setting the service-user identity
    /// </summary>
    internal class UrlStrippingStartupFilter : IStartupFilter
    {
        private static readonly ConcurrentDictionary<string, int> FormPages = new();
        private static readonly ConcurrentDictionary<string, string> FormSelectedRows = new();
        private static readonly ConcurrentDictionary<string, string> FormFilters = new();
        private static readonly ConcurrentDictionary<string, string> SalesLineDescriptions = new();
        private static readonly ConcurrentDictionary<string, bool> InsertedSalesLineForms = new();
        private static readonly ConcurrentDictionary<string, LookupTarget> LookupTargets = new();
        private static readonly ConcurrentDictionary<string, string> PostingConfirmationTargets = new();
        private static readonly ConcurrentDictionary<string, string> DeleteConfirmationTargets = new();

        private readonly System.Collections.Generic.HashSet<int> _boundPorts;

        public UrlStrippingStartupFilter(System.Collections.Generic.HashSet<int> boundPorts)
        {
            _boundPorts = boundPorts;
        }

        public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next)
        {
            return app =>
            {
                // Strip paths from server addresses before the server starts
                var addressFeature = app.ServerFeatures.Get<IServerAddressesFeature>();
                string? pathBase = null;
                if (addressFeature != null && addressFeature.Addresses.Count > 0)
                {
                    var original = addressFeature.Addresses.ToList();
                    addressFeature.Addresses.Clear();
                    foreach (var addr in original)
                    {
                        var (stripped, path) = StripPath(addr);
                        if (stripped != null)
                        {
                            addressFeature.Addresses.Add(stripped);
                            if (!string.IsNullOrEmpty(path))
                                pathBase = path;
                            Console.WriteLine($"[HttpSysStub] {addr} → {stripped}");
                        }
                    }
                }

                if (IsWebClientPathBase(pathBase))
                {
                    app.UseWebSockets();
                    app.Use(WebClientSignInCompatibility);
                }

                if (!string.IsNullOrEmpty(pathBase))
                    app.UsePathBase(pathBase);

                // Set authenticated service-user identity after validating Basic auth.
                // Defaults to admin/admin for the standard container network surface,
                // but honors BC_USERNAME/BC_PASSWORD for custom provider-created users.
                app.Use(async (context, nextMiddleware) =>
                {
                    if (!IsAuthorizedRequest(context))
                    {
                        await RejectUnauthorized(context);
                        return;
                    }

                    var serviceUser = ServiceUserName();
                    var identity = new ClaimsIdentity(new[] {
                        new Claim(ClaimTypes.Name, serviceUser),
                        new Claim(ClaimTypes.Role, "SUPER"),
                        new Claim(ClaimTypes.Role, "AdminService"),
                        new Claim(ClaimTypes.NameIdentifier, "00000000-0000-0000-0000-000000000001"),
                    }, "Passthrough");
                    context.User = new ClaimsPrincipal(identity);
                    await nextMiddleware();
                });

                next(app);
            };
        }

        private static bool IsWebClientPathBase(string? pathBase)
        {
            return string.Equals(pathBase, "/BC/client", StringComparison.OrdinalIgnoreCase);
        }

        private static bool IsAuthorizedRequest(Microsoft.AspNetCore.Http.HttpContext context)
        {
            var path = context.Request.Path.Value?.TrimEnd('/') ?? string.Empty;
            if (IsPublicWebClientCompatibilityPath(path))
                return true;

            if (!context.Request.Headers.TryGetValue("Authorization", out var values))
                return false;

            var header = values.FirstOrDefault();
            if (string.IsNullOrWhiteSpace(header)
                || !header.StartsWith("Basic ", StringComparison.OrdinalIgnoreCase))
                return false;

            var encoded = header.Substring("Basic ".Length).Trim();
            string decoded;
            try
            {
                decoded = Encoding.UTF8.GetString(Convert.FromBase64String(encoded));
            }
            catch (FormatException)
            {
                return false;
            }

            var separator = decoded.IndexOf(':');
            if (separator < 0)
                return false;

            var username = decoded.Substring(0, separator);
            var password = decoded.Substring(separator + 1);
            return string.Equals(username, ServiceUserName(), StringComparison.OrdinalIgnoreCase)
                && string.Equals(password, ServicePassword(), StringComparison.Ordinal);
        }

        private static bool IsPublicWebClientCompatibilityPath(string path)
        {
            return string.Equals(path, "/SignIn", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/SignIn", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/client/SignIn", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/csrf", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/csrf", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/client/csrf", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/client", StringComparison.OrdinalIgnoreCase);
        }

        private static async System.Threading.Tasks.Task RejectUnauthorized(
            Microsoft.AspNetCore.Http.HttpContext context)
        {
            context.Response.StatusCode = 401;
            context.Response.Headers["WWW-Authenticate"] = "Basic realm=\"Business Central\"";
            await context.Response.WriteAsync("Unauthorized");
        }

        private static async System.Threading.Tasks.Task WebClientSignInCompatibility(
            Microsoft.AspNetCore.Http.HttpContext context,
            Func<System.Threading.Tasks.Task> nextMiddleware)
        {
            var path = context.Request.Path.Value?.TrimEnd('/') ?? string.Empty;
            if (string.Equals(path, "/SignIn", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/SignIn", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/client/SignIn", StringComparison.OrdinalIgnoreCase))
            {
                SetShimCookie(context);
                context.Response.StatusCode = 200;
                context.Response.ContentType = "text/html; charset=utf-8";
                await context.Response.WriteAsync(
                    "<html><body><form method=\"post\"><input name=\"__RequestVerificationToken\" type=\"hidden\" value=\"shim-rvt\"/><input name=\"UserName\"/><input name=\"Password\" type=\"password\"/><button type=\"submit\">Sign in</button></form></body></html>");
                return;
            }

            if (string.Equals(path, "/csrf", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/csrf", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/client/csrf", StringComparison.OrdinalIgnoreCase))
            {
                SetShimCookie(context);
                context.Response.StatusCode = 200;
                context.Response.ContentType = "application/json; charset=utf-8";
                await context.Response.WriteAsync("{\"csrfToken\":\"shim-csrf\"}");
                return;
            }

            if (string.Equals(path, "/BC/client", StringComparison.OrdinalIgnoreCase))
            {
                context.Response.Redirect("/BC/client/SignIn");
                return;
            }

            if (IsClientServicesPath(path)
                && !context.Request.Headers.ContainsKey("Authorization")
                && context.Request.Cookies.ContainsKey("BCAuth"))
            {
                context.Request.Headers["Authorization"] = BasicAuthorizationHeader();
            }

            if (IsClientServicesPath(path) && context.WebSockets.IsWebSocketRequest)
            {
                await HandleClientServicesWebSocket(context);
                return;
            }

            await nextMiddleware();
        }

        private static bool IsClientServicesPath(string path)
        {
            return string.Equals(path, "/csh", StringComparison.OrdinalIgnoreCase)
                || path.StartsWith("/csh/", StringComparison.OrdinalIgnoreCase)
                || string.Equals(path, "/BC/client/csh", StringComparison.OrdinalIgnoreCase)
                || path.StartsWith("/BC/client/csh/", StringComparison.OrdinalIgnoreCase);
        }

        private static string ServiceUserName()
        {
            return NonEmptyEnvironment("BC_USERNAME", "admin");
        }

        private static string ServicePassword()
        {
            return NonEmptyEnvironment("BC_PASSWORD", "admin");
        }

        private static string BasicAuthorizationHeader()
        {
            var token = Convert.ToBase64String(
                Encoding.UTF8.GetBytes($"{ServiceUserName()}:{ServicePassword()}"));
            return $"Basic {token}";
        }

        private static string NonEmptyEnvironment(string name, string fallback)
        {
            var value = Environment.GetEnvironmentVariable(name);
            return string.IsNullOrWhiteSpace(value) ? fallback : value;
        }

        private static async System.Threading.Tasks.Task HandleClientServicesWebSocket(
            Microsoft.AspNetCore.Http.HttpContext context)
        {
            using var webSocket = await context.WebSockets.AcceptWebSocketAsync();
            var buffer = new byte[64 * 1024];
            while (webSocket.State == WebSocketState.Open)
            {
                var result = await webSocket.ReceiveAsync(buffer, context.RequestAborted);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    await webSocket.CloseAsync(
                        WebSocketCloseStatus.NormalClosure,
                        "closed",
                        context.RequestAborted);
                    return;
                }
                if (result.MessageType != WebSocketMessageType.Text)
                    continue;

                var request = Encoding.UTF8.GetString(buffer, 0, result.Count);
                var response = BuildClientServicesResponse(request);
                if (response.Length == 0)
                    continue;

                var responseBytes = Encoding.UTF8.GetBytes(response);
                await webSocket.SendAsync(
                    responseBytes,
                    WebSocketMessageType.Text,
                    true,
                    context.RequestAborted);
            }
        }

        private static string BuildClientServicesResponse(string request)
        {
            try
            {
                using var document = JsonDocument.Parse(request);
                var root = document.RootElement;
                if (!root.TryGetProperty("id", out var idElement))
                    return string.Empty;

                var id = idElement.GetString() ?? string.Empty;
                var method = root.TryGetProperty("method", out var methodElement)
                    ? methodElement.GetString() ?? string.Empty
                    : string.Empty;

                if (string.Equals(method, "OpenSession", StringComparison.OrdinalIgnoreCase))
                {
                    return JsonSerializer.Serialize(new
                    {
                        jsonrpc = "2.0",
                        id,
                        result = new object[]
                        {
                            new
                            {
                                handlerType = "DN.SessionInitHandler",
                                parameters = new object[]
                                {
                                    new
                                    {
                                        ServerSessionId = Guid.NewGuid().ToString(),
                                        SessionKey = Guid.NewGuid().ToString(),
                                        CompanyName = "CRONUS International Ltd.",
                                    },
                                },
                            },
                        },
                    });
                }

                if (string.Equals(method, "Invoke", StringComparison.OrdinalIgnoreCase))
                {
                    return JsonSerializer.Serialize(new
                    {
                        jsonrpc = "2.0",
                        id,
                        result = BuildInvokeHandlers(root),
                    });
                }

                return JsonSerializer.Serialize(new
                {
                    jsonrpc = "2.0",
                    id,
                    result = Array.Empty<object>(),
                });
            }
            catch
            {
                return string.Empty;
            }
        }

        private static object[] BuildInvokeHandlers(JsonElement root)
        {
            var interaction = FirstInteraction(root);
            var interactionName = interaction.HasValue
                && interaction.Value.TryGetProperty("interactionName", out var nameElement)
                    ? nameElement.GetString() ?? string.Empty
                    : string.Empty;

            if (string.Equals(interactionName, "OpenForm", StringComparison.OrdinalIgnoreCase))
            {
                var query = OpenFormQuery(interaction);
                return PageFormHandlers(query.PageId, query.Bookmark, query.Filter);
            }

            var handlers = new List<object>();
            foreach (var item in Interactions(root))
            {
                var itemName = item.TryGetProperty("interactionName", out var itemNameElement)
                    ? itemNameElement.GetString() ?? string.Empty
                    : string.Empty;
                handlers.AddRange(BuildInteractionHandlers(itemName, item));
            }

            return handlers.ToArray();
        }

        private static object[] BuildInteractionHandlers(string interactionName, JsonElement interaction)
        {
            if (string.Equals(interactionName, "ActivateControl", StringComparison.OrdinalIgnoreCase)
                || string.Equals(interactionName, "KeepAlive", StringComparison.OrdinalIgnoreCase))
            {
                return new object[] { CompletedInteraction(interaction) };
            }

            if (string.Equals(interactionName, "InvokeAction", StringComparison.OrdinalIgnoreCase))
            {
                var systemAction = SystemAction(interaction);
                if (systemAction == 20)
                {
                    var handlers = new List<object> { CompletedInteraction(interaction) };
                    handlers.AddRange(DeleteConfirmationHandlers(InteractionFormId(interaction)));
                    return handlers.ToArray();
                }

                if (systemAction == 110)
                {
                    var handlers = new List<object> { CompletedInteraction(interaction) };
                    handlers.AddRange(CustomerLookupHandlers(
                        InteractionFormId(interaction),
                        InteractionControlPath(interaction)));
                    return handlers.ToArray();
                }

                var formId = InteractionFormId(interaction);
                if (systemAction == 10
                    && FormPages.TryGetValue(formId, out var actionPageId)
                    && actionPageId == 46)
                {
                    InsertedSalesLineForms[formId] = true;
                    return new object[]
                    {
                        CompletedInteraction(interaction),
                        RowRefreshHandler(
                            formId,
                            new object[]
                            {
                                SalesLineRowInserted(2, "line-new", "Item", string.Empty, string.Empty, "0"),
                            }),
                    };
                }

                if (systemAction == 100
                    && FormPages.TryGetValue(formId, out actionPageId)
                    && actionPageId == 42)
                {
                    var handlers = new List<object> { CompletedInteraction(interaction) };
                    handlers.AddRange(PostConfirmationHandlers(formId));
                    return handlers.ToArray();
                }

                if (systemAction is 10 or 40 or 70)
                {
                    var handlers = new List<object> { CompletedInteraction(interaction) };
                    handlers.AddRange(PageFormHandlers(21, SelectedCustomerBookmark(interaction)));
                    return handlers.ToArray();
                }

                if (systemAction is 330 or 340 or 380 or 390)
                {
                    formId = InteractionFormId(interaction);
                    if (systemAction == 330 && LookupTargets.TryRemove(formId, out var lookupTarget))
                    {
                        var selectedBookmark = SelectedCustomerBookmark(interaction) ?? "cust-10000";
                        var customer = CustomerByBookmark(selectedBookmark);
                        return new object[]
                        {
                            CompletedInteraction(interaction),
                            PropertyChangesHandler(
                                lookupTarget.FormId,
                                lookupTarget.ControlPath,
                                customer.Number),
                            PropertyChangesHandler(
                                lookupTarget.FormId,
                                "server:c[3]",
                                customer.Name),
                            FormClosedHandler(formId),
                        };
                    }

                    if (systemAction == 380
                        && DeleteConfirmationTargets.TryRemove(formId, out var deleteTargetFormId))
                    {
                        return new object[]
                        {
                            CompletedInteraction(interaction),
                            FormClosedHandler(formId),
                            RowRefreshHandler(
                                deleteTargetFormId,
                                new object[]
                                {
                                    RowDeleted(0),
                                }),
                        };
                    }

                    if (systemAction == 380
                        && PostingConfirmationTargets.TryRemove(formId, out var targetFormId))
                    {
                        var handlers = new List<object>
                        {
                            CompletedInteraction(interaction),
                            FormClosedHandler(formId),
                        };
                        handlers.AddRange(PostedMessageHandlers(targetFormId));
                        return handlers.ToArray();
                    }

                    FormPages.TryRemove(formId, out _);
                    FormSelectedRows.TryRemove(formId, out _);
                    FormFilters.TryRemove(formId, out _);
                    InsertedSalesLineForms.TryRemove(formId, out _);
                    LookupTargets.TryRemove(formId, out _);
                    PostingConfirmationTargets.TryRemove(formId, out _);
                    DeleteConfirmationTargets.TryRemove(formId, out _);
                    return new object[]
                    {
                        CompletedInteraction(interaction),
                        FormClosedHandler(formId),
                    };
                }

                if (systemAction == 50)
                {
                    return new object[]
                    {
                        CompletedInteraction(interaction),
                        FormEditableHandler(InteractionFormId(interaction), true),
                    };
                }

                return new object[] { CompletedInteraction(interaction) };
            }

            if (string.Equals(interactionName, "Navigate", StringComparison.OrdinalIgnoreCase))
            {
                var handlers = new List<object> { CompletedInteraction(interaction) };
                handlers.AddRange(PageFormHandlers(NavigatePageId(interaction)));
                return handlers.ToArray();
            }

            if (string.Equals(interactionName, "SaveValue", StringComparison.OrdinalIgnoreCase))
            {
                var formId = InteractionFormId(interaction);
                var controlPath = InteractionControlPath(interaction);
                var value = SaveValue(interaction);
                if (IsSalesLineQuantityControl(formId, controlPath)
                    && decimal.TryParse(value, out var quantity)
                    && quantity < 0)
                {
                    return new object[]
                    {
                        CompletedInteraction(interaction),
                        PropertyValidationHandler(
                            formId,
                            controlPath,
                            "Quantity must be greater than or equal to 0."),
                    };
                }

                return new object[]
                {
                    CompletedInteraction(interaction),
                    PropertyChangesHandler(
                        formId,
                        controlPath,
                        value),
                };
            }

            if (string.Equals(interactionName, "SetCurrentRow", StringComparison.OrdinalIgnoreCase))
            {
                var formId = InteractionFormId(interaction);
                var selectedBookmark = SelectedRowBookmark(interaction);
                if (!string.IsNullOrEmpty(formId) && !string.IsNullOrEmpty(selectedBookmark))
                    FormSelectedRows[formId] = selectedBookmark;

                return new object[]
                {
                    CompletedInteraction(interaction),
                    RowRefreshHandler(
                        formId,
                        CustomerRows(22, selectedBookmark: selectedBookmark)),
                };
            }

            if (string.Equals(interactionName, "ScrollRepeater", StringComparison.OrdinalIgnoreCase))
            {
                return new object[]
                {
                    CompletedInteraction(interaction),
                    RowRefreshHandler(
                        InteractionFormId(interaction),
                        new object[]
                        {
                            RowInserted(2, "cust-30000", "30000", "School of Fine Art"),
                            RowInserted(3, "cust-40000", "40000", "Alpine Ski House"),
                        }),
                };
            }

            if (string.Equals(interactionName, "Filter", StringComparison.OrdinalIgnoreCase))
            {
                return new object[]
                {
                    CompletedInteraction(interaction),
                    RowRefreshHandler(
                        InteractionFormId(interaction),
                        new object[]
                        {
                            RowInserted(0, "cust-10000", "10000", "Adatum Corporation"),
                        }),
                };
            }

            if (string.Equals(interactionName, "CloseForm", StringComparison.OrdinalIgnoreCase))
            {
                var formId = InteractionFormId(interaction);
                FormPages.TryRemove(formId, out _);
                FormSelectedRows.TryRemove(formId, out _);
                FormFilters.TryRemove(formId, out _);
                InsertedSalesLineForms.TryRemove(formId, out _);
                PostingConfirmationTargets.TryRemove(formId, out _);
                DeleteConfirmationTargets.TryRemove(formId, out _);
                return new object[]
                {
                    FormClosedHandler(formId),
                };
            }

            if (string.Equals(interactionName, "LoadForm", StringComparison.OrdinalIgnoreCase))
            {
                var formId = InteractionFormId(interaction);
                var pageId = FormPages.TryGetValue(formId, out var knownPageId) ? knownPageId : 0;
                var customerBookmark = FormSelectedRows.TryGetValue(formId, out var knownBookmark)
                    ? knownBookmark
                    : null;
                var filter = FormFilters.TryGetValue(formId, out var knownFilter)
                    ? knownFilter
                    : null;
                return new object[]
                {
                    CompletedInteraction(interaction),
                    DataRefreshHandler(formId, pageId, customerBookmark, filter),
                };
            }

            return Array.Empty<object>();
        }

        private static object[] DeleteConfirmationHandlers(string targetFormId)
        {
            var formId = Guid.NewGuid().ToString();
            FormPages[formId] = 0;
            DeleteConfirmationTargets[formId] = targetFormId;

            return new object[]
            {
                new
                {
                    handlerType = "DN.LogicalClientEventRaisingHandler",
                    parameters = new object[]
                    {
                        "DialogToShow",
                        new
                        {
                            ServerId = formId,
                            Caption = "Confirm",
                            IsModal = true,
                            IsTaskDialog = true,
                            Message = "Do you want to delete the selected customer?",
                            Children = new object[]
                            {
                                new
                                {
                                    t = "ac",
                                    Name = "Yes",
                                    DesignName = "Yes",
                                    Caption = "Yes",
                                    SystemAction = 380,
                                    Enabled = true,
                                },
                                new
                                {
                                    t = "ac",
                                    Name = "No",
                                    DesignName = "No",
                                    Caption = "No",
                                    SystemAction = 390,
                                    Enabled = true,
                                },
                            },
                        },
                    },
                },
            };
        }

        private static object[] PostConfirmationHandlers(string targetFormId)
        {
            var formId = Guid.NewGuid().ToString();
            FormPages[formId] = 0;
            PostingConfirmationTargets[formId] = targetFormId;

            return new object[]
            {
                new
                {
                    handlerType = "DN.LogicalClientEventRaisingHandler",
                    parameters = new object[]
                    {
                        "DialogToShow",
                        new
                        {
                            ServerId = formId,
                            Caption = "Confirm",
                            IsModal = true,
                            IsTaskDialog = true,
                            Message = "Do you want to post the sales order?",
                            Children = YesNoDialogActions(),
                        },
                    },
                },
            };
        }

        private static object[] PostedMessageHandlers(string targetFormId)
        {
            var formId = Guid.NewGuid().ToString();
            FormPages[formId] = 0;

            return new object[]
            {
                new
                {
                    handlerType = "DN.LogicalClientEventRaisingHandler",
                    parameters = new object[]
                    {
                        "DialogToShow",
                        new
                        {
                            ServerId = formId,
                            Caption = "Posted",
                            IsModal = true,
                            IsTaskDialog = true,
                            Message = "The sales order has been posted.",
                            ParentServerId = targetFormId,
                            Children = new object[]
                            {
                                new
                                {
                                    t = "ac",
                                    Name = "OK",
                                    DesignName = "OK",
                                    Caption = "OK",
                                    SystemAction = 330,
                                    Enabled = true,
                                },
                            },
                        },
                    },
                },
            };
        }

        private static object[] YesNoDialogActions()
        {
            return new object[]
            {
                new
                {
                    t = "ac",
                    Name = "Yes",
                    DesignName = "Yes",
                    Caption = "Yes",
                    SystemAction = 380,
                    Enabled = true,
                },
                new
                {
                    t = "ac",
                    Name = "No",
                    DesignName = "No",
                    Caption = "No",
                    SystemAction = 390,
                    Enabled = true,
                },
            };
        }

        private static object[] CustomerLookupHandlers(string targetFormId, string targetControlPath)
        {
            var formId = Guid.NewGuid().ToString();
            FormPages[formId] = 22;
            FormSelectedRows[formId] = "cust-10000";
            LookupTargets[formId] = new LookupTarget(targetFormId, targetControlPath);

            return new object[]
            {
                new
                {
                    handlerType = "DN.LogicalClientEventRaisingHandler",
                    parameters = new object[]
                    {
                        "DialogToShow",
                        new
                        {
                            ServerId = formId,
                            Caption = "Customers",
                            IsModal = true,
                            Metadata = new
                            {
                                id = 22,
                                sourceTableId = 18,
                            },
                            Children = new object[]
                            {
                                new
                                {
                                    t = "rc",
                                    Name = "CustomerList",
                                    DesignName = "Repeater",
                                    Caption = "Customers",
                                    Columns = new object[]
                                    {
                                        new
                                        {
                                            Name = "No",
                                            DesignName = "No.",
                                            Caption = "No.",
                                            Editable = false,
                                        },
                                        new
                                        {
                                            Name = "Name",
                                            DesignName = "Name",
                                            Caption = "Name",
                                            Editable = false,
                                        },
                                    },
                                },
                                new
                                {
                                    t = "ac",
                                    Name = "OK",
                                    DesignName = "OK",
                                    Caption = "OK",
                                    SystemAction = 330,
                                    Enabled = true,
                                },
                                new
                                {
                                    t = "ac",
                                    Name = "Cancel",
                                    DesignName = "Cancel",
                                    Caption = "Cancel",
                                    SystemAction = 340,
                                    Enabled = true,
                                },
                            },
                        },
                    },
                },
                DataRefreshHandler(formId, 22, "cust-10000"),
            };
        }

        private static object[] PageFormHandlers(
            int pageId,
            string? customerBookmark = null,
            string? filter = null)
        {
            var formId = Guid.NewGuid().ToString();
            FormPages[formId] = pageId;
            if (!string.IsNullOrEmpty(customerBookmark))
                FormSelectedRows[formId] = customerBookmark;
            if (!string.IsNullOrEmpty(filter))
                FormFilters[formId] = filter;

            return new object[]
            {
                FormToShowHandler(formId, pageId),
                DataRefreshHandler(formId, pageId, customerBookmark, filter),
            };
        }

        private static object FormToShowHandler(string formId, int pageId)
        {
            return new
            {
                handlerType = "DN.LogicalClientEventRaisingHandler",
                parameters = new object[]
                {
                    "FormToShow",
                    new
                    {
                        ServerId = formId,
                        Caption = PageCaption(pageId),
                        Editable = pageId == 21,
                        IsModal = false,
                        Metadata = new
                        {
                            id = pageId,
                            sourceTableId = SourceTableId(pageId),
                        },
                        Children = PageChildren(pageId, formId),
                    },
                },
            };
        }

        private static object FormClosedHandler(string formId)
        {
            return new
            {
                handlerType = "DN.LogicalClientChangeHandler",
                parameters = new object[]
                {
                    formId,
                    new object[]
                    {
                        new
                        {
                            t = "PropertyChange",
                            PropertyName = "State",
                            PropertyValue = 0,
                            ControlReference = new
                            {
                                formId,
                            },
                        },
                    },
                },
            };
        }

        private static object DataRefreshHandler(
            string formId,
            int pageId,
            string? customerBookmark = null,
            string? filter = null)
        {
            var controlPath = pageId == 21 ? "server:" : "server:c[1]";
            return new
            {
                handlerType = "DN.LogicalClientChangeHandler",
                parameters = new object[]
                {
                    formId,
                    new object[]
                    {
                        new
                        {
                            t = "DataRefreshChange",
                            ControlReference = new
                            {
                                formId,
                                controlPath,
                            },
                            RowChanges = CustomerRows(
                                pageId,
                                formId: formId,
                                selectedBookmark: customerBookmark,
                                filter: filter),
                        },
                    },
                },
            };
        }

        private static object FormEditableHandler(string formId, bool editable)
        {
            return new
            {
                handlerType = "DN.LogicalClientChangeHandler",
                parameters = new object[]
                {
                    formId,
                    new object[]
                    {
                        new
                        {
                            t = "PropertyChange",
                            PropertyName = "Editable",
                            PropertyValue = editable,
                            ControlReference = new
                            {
                                formId,
                            },
                        },
                    },
                },
            };
        }

        private static object PropertyChangesHandler(string formId, string controlPath, string value)
        {
            var caption = CaptionForControlPath(controlPath);
            return new
            {
                handlerType = "DN.LogicalClientChangeHandler",
                parameters = new object[]
                {
                    formId,
                    new object[]
                    {
                        new
                        {
                            t = "PropertyChanges",
                            ControlReference = new
                            {
                                formId,
                                controlPath,
                            },
                            Changes = new
                            {
                                Caption = caption,
                                StringValue = value,
                            },
                        },
                    },
                },
            };
        }

        private static object PropertyValidationHandler(string formId, string controlPath, string message)
        {
            var caption = CaptionForControlPath(controlPath);
            return new
            {
                handlerType = "DN.LogicalClientChangeHandler",
                parameters = new object[]
                {
                    formId,
                    new object[]
                    {
                        new
                        {
                            t = "PropertyChanges",
                            ControlReference = new
                            {
                                formId,
                                controlPath,
                            },
                            Changes = new
                            {
                                Caption = caption,
                                ValidationMessage = new
                                {
                                    message,
                                    severity = "error",
                                },
                            },
                        },
                    },
                },
            };
        }

        private static string? CaptionForControlPath(string controlPath)
        {
            return controlPath switch
            {
                "server:c[2]" => "Sell-to Customer No.",
                "server:c[3]" => "Sell-to Customer Name",
                "server:c[0]/cr/c[2]" => "Description",
                "server:c[0]/cr/c[3]" => "Quantity",
                _ => null,
            };
        }

        private static bool IsSalesLineQuantityControl(string formId, string controlPath)
        {
            return FormPages.TryGetValue(formId, out var pageId)
                && pageId == 46
                && string.Equals(controlPath, "server:c[0]/cr/c[3]", StringComparison.Ordinal);
        }

        private static object RowRefreshHandler(string formId, object[] rowChanges)
        {
            return new
            {
                handlerType = "DN.LogicalClientChangeHandler",
                parameters = new object[]
                {
                    formId,
                    new object[]
                    {
                        new
                        {
                            t = "DataRefreshChange",
                            ControlReference = new
                            {
                                formId,
                                controlPath = "server:c[1]",
                            },
                            RowChanges = rowChanges,
                        },
                    },
                },
            };
        }

        private static string InteractionFormId(JsonElement interaction)
        {
            return interaction.TryGetProperty("formId", out var formIdElement)
                ? formIdElement.GetString() ?? string.Empty
                : string.Empty;
        }

        private static string InteractionFormId(JsonElement? interaction)
        {
            return interaction.HasValue
                && interaction.Value.TryGetProperty("formId", out var formIdElement)
                    ? formIdElement.GetString() ?? string.Empty
                    : string.Empty;
        }

        private static string InteractionControlPath(JsonElement interaction)
        {
            return interaction.TryGetProperty("controlPath", out var controlPathElement)
                ? controlPathElement.GetString() ?? string.Empty
                : string.Empty;
        }

        private static int? SystemAction(JsonElement interaction)
        {
            if (!interaction.TryGetProperty("namedParameters", out var namedParameters)
                || namedParameters.ValueKind != JsonValueKind.String)
                return null;

            using var document = JsonDocument.Parse(namedParameters.GetString() ?? "{}");
            return document.RootElement.TryGetProperty("systemAction", out var systemAction)
                && systemAction.TryGetInt32(out var value)
                    ? value
                    : null;
        }

        private static string SelectedRowBookmark(JsonElement interaction)
        {
            if (!interaction.TryGetProperty("namedParameters", out var namedParameters)
                || namedParameters.ValueKind != JsonValueKind.String)
                return string.Empty;

            using var document = JsonDocument.Parse(namedParameters.GetString() ?? "{}");
            return document.RootElement.TryGetProperty("key", out var key)
                ? key.GetString() ?? string.Empty
                : string.Empty;
        }

        private static int NavigatePageId(JsonElement interaction)
        {
            if (!interaction.TryGetProperty("namedParameters", out var namedParameters)
                || namedParameters.ValueKind != JsonValueKind.String)
                return 0;

            using var document = JsonDocument.Parse(namedParameters.GetString() ?? "{}");
            var nodeId = document.RootElement.TryGetProperty("nodeId", out var nodeIdElement)
                ? nodeIdElement.GetString() ?? string.Empty
                : string.Empty;

            return nodeId.Contains("customer", StringComparison.OrdinalIgnoreCase)
                ? 22
                : 0;
        }

        private static string? SelectedCustomerBookmark(JsonElement interaction)
        {
            var formId = InteractionFormId(interaction);
            return !string.IsNullOrEmpty(formId) && FormSelectedRows.TryGetValue(formId, out var bookmark)
                ? bookmark
                : null;
        }

        private static string SaveValue(JsonElement interaction)
        {
            if (!interaction.TryGetProperty("namedParameters", out var namedParameters)
                || namedParameters.ValueKind != JsonValueKind.String)
                return string.Empty;

            using var document = JsonDocument.Parse(namedParameters.GetString() ?? "{}");
            return document.RootElement.TryGetProperty("newValue", out var newValue)
                ? newValue.GetString() ?? string.Empty
                : string.Empty;
        }

        private static object CompletedInteraction(JsonElement interaction)
        {
            var callbackId = interaction.TryGetProperty("callbackId", out var callbackIdElement)
                    ? callbackIdElement.GetString() ?? string.Empty
                    : string.Empty;

            return CompletedInteraction(callbackId);
        }

        private static object CompletedInteraction(JsonElement? interaction)
        {
            var callbackId = interaction.HasValue
                && interaction.Value.TryGetProperty("callbackId", out var callbackIdElement)
                    ? callbackIdElement.GetString() ?? string.Empty
                    : string.Empty;

            return CompletedInteraction(callbackId);
        }

        private static object CompletedInteraction(string callbackId)
        {
            return new
            {
                handlerType = "DN.CallbackResponseProperties",
                parameters = new object[]
                {
                    new
                    {
                        SequenceNumber = 1,
                        CompletedInteractions = new object[]
                        {
                            new
                            {
                                InvocationId = callbackId,
                                Duration = 0.0,
                                Result = new
                                {
                                    reason = 0,
                                    value = string.Empty,
                                },
                            },
                        },
                    },
                },
            };
        }

        private static object[] PageChildren(int pageId, string formId)
        {
            return pageId switch
            {
                21 => CustomerCardChildren(),
                22 => CustomerListChildren(),
                42 => SalesOrderChildren(formId),
                46 => SalesLineSubpageChildren(),
                _ => Array.Empty<object>(),
            };
        }

        private static object[] CustomerListChildren()
        {
            return new object[]
            {
                new
                {
                    t = "fvc",
                    Name = "SearchFilter",
                    DesignName = "SearchFilter",
                    Caption = "Search",
                    Editable = true,
                },
                new
                {
                    t = "rc",
                    Name = "CustomerList",
                    DesignName = "Repeater",
                    Caption = "Customers",
                    Columns = new object[]
                    {
                        new
                        {
                            Name = "No",
                            DesignName = "No.",
                            Caption = "No.",
                            Editable = false,
                        },
                        new
                        {
                            Name = "Name",
                            DesignName = "Name",
                            Caption = "Name",
                            Editable = false,
                        },
                    },
                },
                new
                {
                    t = "ac",
                    Name = "View",
                    DesignName = "View",
                    Caption = "View",
                    SystemAction = 70,
                    Enabled = true,
                },
                new
                {
                    t = "ac",
                    Name = "New",
                    DesignName = "New",
                    Caption = "New",
                    SystemAction = 10,
                    Enabled = true,
                },
                new
                {
                    t = "ac",
                    Name = "Delete",
                    DesignName = "Delete",
                    Caption = "Delete",
                    SystemAction = 20,
                    Enabled = true,
                },
            };
        }

        private static object[] CustomerCardChildren()
        {
            return new object[]
            {
                FieldControl("No.", "No", "No.", true),
                FieldControl("Name", "Name", "Name", true),
                FieldControl("Address", "Address", "Address", true),
                FieldControl("City", "City", "City", true),
                FieldControl("Phone No.", "PhoneNo", "Phone No.", true),
                new
                {
                    t = "ac",
                    Name = "Edit",
                    DesignName = "Edit",
                    Caption = "Edit",
                    SystemAction = 50,
                    Enabled = true,
                },
            };
        }

        private static object[] SalesOrderChildren(string formId)
        {
            var linesFormId = $"{formId}:lines";
            FormPages[linesFormId] = 46;

            return new object[]
            {
                FieldControl("Document Type", "DocumentType", "Document Type", false, "Order"),
                FieldControl("No.", "No", "No.", true, "SO1001"),
                FieldControl("Sell-to Customer No.", "SellToCustomerNo", "Sell-to Customer No.", true, "10000", 110),
                FieldControl("Sell-to Customer Name", "SellToCustomerName", "Sell-to Customer Name", false, "Adatum Corporation"),
                new
                {
                    t = "lf",
                    Name = "SalesLines",
                    DesignName = "SalesLines",
                    Caption = "Sales Lines",
                    ServerId = linesFormId,
                    Children = SalesLineSubpageChildren(),
                },
                new
                {
                    t = "ac",
                    Name = "Post",
                    DesignName = "Post",
                    Caption = "Post",
                    SystemAction = 100,
                    Enabled = true,
                },
            };
        }

        private static object[] SalesLineSubpageChildren()
        {
            return new object[]
            {
                new
                {
                    t = "rc",
                    Name = "SalesLines",
                    DesignName = "Repeater",
                    Caption = "Sales Lines",
                    Columns = new object[]
                    {
                        new
                        {
                            Name = "Type",
                            DesignName = "Type",
                            Caption = "Type",
                            Editable = true,
                        },
                        new
                        {
                            Name = "No",
                            DesignName = "No.",
                            Caption = "No.",
                            Editable = true,
                        },
                        new
                        {
                            Name = "Description",
                            DesignName = "Description",
                            Caption = "Description",
                            Editable = true,
                        },
                        new
                        {
                            Name = "Quantity",
                            DesignName = "Quantity",
                            Caption = "Quantity",
                            Editable = true,
                        },
                    },
                },
                new
                {
                    t = "ac",
                    Name = "NewLine",
                    DesignName = "New Line",
                    Caption = "New Line",
                    SystemAction = 10,
                    Enabled = true,
                },
            };
        }

        private static object FieldControl(string caption, string name, string designName, bool editable)
        {
            return FieldControl(caption, name, designName, editable, string.Empty);
        }

        private static object FieldControl(
            string caption,
            string name,
            string designName,
            bool editable,
            string value)
        {
            return FieldControl(caption, name, designName, editable, value, null);
        }

        private static object FieldControl(
            string caption,
            string name,
            string designName,
            bool editable,
            string value,
            int? systemAction)
        {
            return new
            {
                t = "fc",
                Name = name,
                DesignName = designName,
                Caption = caption,
                Editable = editable,
                StringValue = value,
                SystemAction = systemAction,
            };
        }

        private static object[] CustomerRows(
            int pageId,
            string? formId = null,
            string? selectedBookmark = null,
            string? filter = null)
        {
            if (pageId == 21)
            {
                var customer = CustomerByBookmark(selectedBookmark ?? "cust-10000");
                return new object[]
                {
                    RowInserted(
                        0,
                        customer.Bookmark,
                        customer.Number,
                        customer.Name,
                        true,
                        customer.Address,
                        customer.City,
                        customer.Phone),
                };
            }

            if (pageId == 42)
                return Array.Empty<object>();

            if (pageId == 46)
                return SalesLineRows(formId);

            if (pageId != 22)
                return Array.Empty<object>();

            var rows = new List<object>();
            if (string.IsNullOrEmpty(filter)
                || filter.Contains("Adatum", StringComparison.OrdinalIgnoreCase)
                || filter.Contains("10000", StringComparison.OrdinalIgnoreCase))
            {
                rows.Add(RowInserted(
                    0,
                    "cust-10000",
                    "10000",
                    "Adatum Corporation",
                    selectedBookmark == null || selectedBookmark == "cust-10000"));
            }

            if (string.IsNullOrEmpty(filter)
                || filter.Contains("Trey", StringComparison.OrdinalIgnoreCase)
                || filter.Contains("20000", StringComparison.OrdinalIgnoreCase))
            {
                rows.Add(RowInserted(
                    rows.Count,
                    "cust-20000",
                    "20000",
                    "Trey Research",
                    selectedBookmark == "cust-20000"));
            }

            return rows.ToArray();
        }

        private static object[] SalesLineRows(string? formId)
        {
            var rows = new List<object>
            {
                SalesLineRowInserted(0, "line-10000", "Item", "1896-S", SalesLineDescription("line-10000"), "2"),
                SalesLineRowInserted(1, "line-20000", "G/L Account", "6110", SalesLineDescription("line-20000"), "1"),
            };

            if (!string.IsNullOrEmpty(formId) && InsertedSalesLineForms.ContainsKey(formId))
                rows.Add(SalesLineRowInserted(2, "line-new", "Item", string.Empty, string.Empty, "0"));

            return rows.ToArray();
        }

        private static string SalesLineDescription(string bookmark)
        {
            if (SalesLineDescriptions.TryGetValue(bookmark, out var description))
                return description;

            return bookmark == "line-20000" ? "Freight" : "ATHENS Desk";
        }

        private static object SalesLineRowInserted(
            int index,
            string bookmark,
            string type,
            string number,
            string description,
            string quantity)
        {
            return new
            {
                t = "DataRowInserted",
                DataRowInserted = new object[]
                {
                    index,
                    new
                    {
                        bookmark,
                        selected = index == 0,
                        Type = type,
                        No = number,
                        Description = description,
                        Quantity = quantity,
                    },
                },
            };
        }

        private static object RowInserted(int index, string bookmark, string number, string name)
        {
            return RowChange("DataRowInserted", index, bookmark, number, name, index == 0);
        }

        private static object RowDeleted(int index)
        {
            return new
            {
                t = "DataRowDeleted",
                DataRowDeleted = new object[]
                {
                    index,
                },
            };
        }

        private static object RowUpdated(
            int index,
            string bookmark,
            string number,
            string name,
            bool selected)
        {
            return RowChange("DataRowUpdated", index, bookmark, number, name, selected);
        }

        private static object RowChange(
            string changeType,
            int index,
            string bookmark,
            string number,
            string name,
            bool selected)
        {
            return new
            {
                t = changeType,
                DataRowInserted = changeType == "DataRowInserted"
                    ? new object[]
                    {
                        index,
                        new
                        {
                            bookmark,
                            selected,
                            No = number,
                            Name = name,
                        },
                    }
                    : null,
                DataRowUpdated = changeType == "DataRowUpdated"
                    ? new object[]
                    {
                        index,
                        new
                        {
                            bookmark,
                            selected,
                            No = number,
                            Name = name,
                        },
                    }
                    : null,
            };
        }

        private static object RowInserted(
            int index,
            string bookmark,
            string number,
            string name,
            bool selected)
        {
            return RowInserted(
                index,
                bookmark,
                number,
                name,
                selected,
                string.Empty,
                string.Empty,
                string.Empty);
        }

        private static object RowInserted(
            int index,
            string bookmark,
            string number,
            string name,
            bool selected,
            string address,
            string city,
            string phone)
        {
            return new
            {
                t = "DataRowInserted",
                DataRowInserted = new object[]
                {
                    index,
                    new
                    {
                        bookmark,
                        selected,
                        No = number,
                        Name = name,
                        Address = address,
                        City = city,
                        PhoneNo = phone,
                    },
                },
            };
        }

        private static (string Bookmark, string Number, string Name, string Address, string City, string Phone) CustomerByBookmark(
            string bookmark)
        {
            return bookmark switch
            {
                "cust-20000" => ("cust-20000", "20000", "Trey Research", "192 Market Square", "Atlanta", "425-555-0185"),
                "cust-30000" => ("cust-30000", "30000", "School of Fine Art", "100 Lake Avenue", "Miami", "425-555-0190"),
                "cust-40000" => ("cust-40000", "40000", "Alpine Ski House", "447 Main Road", "Denver", "425-555-0195"),
                _ => ("cust-10000", "10000", "Adatum Corporation", "Station Road 21", "Chicago", "425-555-0100"),
            };
        }

        private static JsonElement? FirstInteraction(JsonElement root)
        {
            if (!root.TryGetProperty("params", out var paramsElement)
                || paramsElement.ValueKind != JsonValueKind.Array
                || paramsElement.GetArrayLength() == 0)
                return null;

            var invokeParams = paramsElement[0];
            if (!invokeParams.TryGetProperty("interactionsToInvoke", out var interactions)
                || interactions.ValueKind != JsonValueKind.Array
                || interactions.GetArrayLength() == 0)
                return null;

            return interactions[0];
        }

        private static IEnumerable<JsonElement> Interactions(JsonElement root)
        {
            if (!root.TryGetProperty("params", out var paramsElement)
                || paramsElement.ValueKind != JsonValueKind.Array
                || paramsElement.GetArrayLength() == 0)
                yield break;

            var invokeParams = paramsElement[0];
            if (!invokeParams.TryGetProperty("interactionsToInvoke", out var interactions)
                || interactions.ValueKind != JsonValueKind.Array)
                yield break;

            foreach (var interaction in interactions.EnumerateArray())
                yield return interaction;
        }

        private static OpenFormQueryResult OpenFormQuery(JsonElement? interaction)
        {
            if (!interaction.HasValue
                || !interaction.Value.TryGetProperty("namedParameters", out var namedParameters)
                || namedParameters.ValueKind != JsonValueKind.String)
                return new OpenFormQueryResult(0, null, null);

            var raw = namedParameters.GetString() ?? string.Empty;
            using var document = JsonDocument.Parse(raw);
            if (!document.RootElement.TryGetProperty("query", out var query)
                || query.ValueKind != JsonValueKind.String)
                return new OpenFormQueryResult(0, null, null);

            var value = query.GetString() ?? string.Empty;
            var parts = value
                .Split('&', StringSplitOptions.RemoveEmptyEntries)
                .Select(part => part.Split('=', 2))
                .Where(part => part.Length == 2)
                .ToDictionary(
                    part => part[0],
                    part => part[1].Trim('\''),
                    StringComparer.OrdinalIgnoreCase);

            if (parts.TryGetValue("page", out var pageValue)
                && int.TryParse(pageValue, out var pageId))
            {
                return new OpenFormQueryResult(
                    pageId,
                    parts.TryGetValue("bookmark", out var bookmark) ? bookmark : null,
                    parts.TryGetValue("filter", out var filter) ? filter : null);
            }

            if (parts.TryGetValue("search", out var search))
            {
                return new OpenFormQueryResult(
                    search.Contains("customer", StringComparison.OrdinalIgnoreCase) ? 22 : 0,
                    null,
                    null);
            }

            return new OpenFormQueryResult(0, null, null);
        }

        private readonly record struct OpenFormQueryResult(
            int PageId,
            string? Bookmark,
            string? Filter);

        private readonly record struct LookupTarget(
            string FormId,
            string ControlPath);

        private static string PageCaption(int pageId)
        {
            return pageId switch
            {
                21 => "Customer Card",
                22 => "Customer List",
                42 => "Sales Order",
                46 => "Sales Lines",
                _ => $"Page {pageId}",
            };
        }

        private static int SourceTableId(int pageId)
        {
            return pageId switch
            {
                21 or 22 => 18,
                42 => 36,
                46 => 37,
                _ => 0,
            };
        }

        private static void SetShimCookie(Microsoft.AspNetCore.Http.HttpContext context)
        {
            context.Response.Headers.Append("Set-Cookie", "BCAuth=shim; Path=/BC/client; HttpOnly; SameSite=Lax");
        }

        private (string? address, string? pathBase) StripPath(string url)
        {
            try
            {
                var parsed = url.Replace("://+:", "://localhost:").Replace("://*:", "://localhost:");
                var uri = new Uri(parsed);
                var scheme = url.Substring(0, url.IndexOf("://"));
                var host = url.Contains("://+:") ? "+" : url.Contains("://*:") ? "*" : uri.Host;
                var port = uri.Port;
                var path = uri.AbsolutePath.TrimEnd('/');

                lock (_boundPorts)
                {
                    if (!_boundPorts.Add(port))
                        while (!_boundPorts.Add(++port)) { }
                }
                return ($"{scheme}://{host}:{port}", string.IsNullOrEmpty(path) || path == "/" ? null : path);
            }
            catch
            {
                return (url, null);
            }
        }
    }
}
