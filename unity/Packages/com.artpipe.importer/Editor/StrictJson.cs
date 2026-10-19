using System;
using System.Reflection;
using Newtonsoft.Json;
using Newtonsoft.Json.Serialization;

namespace Artpipe
{
    /// <summary>Marks a member that must be PRESENT but may be null (e.g. tool_error).</summary>
    [AttributeUsage(AttributeTargets.Property | AttributeTargets.Field)]
    public sealed class JsonNullableAttribute : Attribute { }

    /// <summary>
    /// Strict JSON reading for every file artpipe exchanges between Python and C#
    /// (conventions lock, Maya reports): snake_case names, every member
    /// required, unknown members rejected. Newtonsoft's defaults would load a
    /// missing number as 0 and silently drop keys this code doesn't know.
    /// </summary>
    public static class StrictJson
    {
        public static readonly JsonSerializerSettings Settings = new JsonSerializerSettings
        {
            ContractResolver = new Resolver(),
            MissingMemberHandling = MissingMemberHandling.Error,
            // Floats like 0.1 must come back as the exact double Python wrote.
            FloatParseHandling = FloatParseHandling.Double,
        };

        public static JsonSerializer Serializer() => JsonSerializer.Create(Settings);

        sealed class Resolver : DefaultContractResolver
        {
            public Resolver()
            {
                // Dictionary keys (class names, map suffixes, file names) stay as written.
                NamingStrategy = new SnakeCaseNamingStrategy { ProcessDictionaryKeys = false };
            }

            protected override JsonProperty CreateProperty(MemberInfo member, MemberSerialization ms)
            {
                var p = base.CreateProperty(member, ms);
                if (!p.Ignored)
                    p.Required = member.GetCustomAttribute<JsonNullableAttribute>() != null
                        ? Required.AllowNull   // key required, value may be null
                        : Required.Always;     // key required, value never null
                return p;
            }
        }
    }
}
