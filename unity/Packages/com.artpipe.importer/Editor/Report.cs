using System.Collections.Generic;
using System.Linq;

namespace Artpipe
{
    /// <summary>Exit codes, same contract as the Phase 1 Maya CLI.</summary>
    public static class ExitCode
    {
        public const int Pass = 0;      // everything passed
        public const int Fail = 1;      // an ASSET is bad: actionable, fix the asset
        public const int ToolError = 2; // the TOOL broke: retry or alert a human
    }

    public static class Severity
    {
        public const string Error = "error";
        public const string Warning = "warning";
        public const string Info = "info";
    }

    /// <summary>
    /// One check result. Same shape as Phase 1:
    /// <c>{id, passed, severity, metrics, message, fixable, offenders}</c>.
    /// </summary>
    public sealed class CheckResult
    {
        public string Id;
        public bool Passed;
        public string Severity;
        public string Message;
        public bool Fixable;
        public SortedDictionary<string, object> Metrics = new SortedDictionary<string, object>();
        public List<string> Offenders = new List<string>();

        public bool Blocking => !Passed && Severity == Artpipe.Severity.Error;

        public Dictionary<string, object> ToDict() => new Dictionary<string, object>
        {
            ["id"] = Id,
            ["passed"] = Passed,
            ["severity"] = Severity,
            ["message"] = Message,
            ["fixable"] = Fixable,
            ["metrics"] = new Dictionary<string, object>(Metrics),
            ["offenders"] = Offenders.OrderBy(o => o, System.StringComparer.Ordinal).Cast<object>().ToList(),
        };
    }

    /// <summary>
    /// The Unity-side report. Step 1 only carries the environment preflight;
    /// per-asset results arrive in later steps.
    /// </summary>
    public sealed class ImportReport
    {
        public const string SchemaVersion = "0.1";
        public const string Tool = "artpipe.unity 0.1.0";

        public List<CheckResult> Preflight = new List<CheckResult>();
        public string ToolError;

        public string Status =>
            ToolError != null || Preflight.Any(r => r.Blocking) ? "error" : "pass";

        public int ExitCode => Status == "pass" ? Artpipe.ExitCode.Pass
            : Status == "fail" ? Artpipe.ExitCode.Fail
            : Artpipe.ExitCode.ToolError;

        public string ToJson() => CanonicalJson.Serialize(new Dictionary<string, object>
        {
            ["schema_version"] = SchemaVersion,
            ["tool"] = Tool,
            ["status"] = Status,
            ["tool_error"] = ToolError,
            ["preflight"] = Preflight.OrderBy(r => r.Id, System.StringComparer.Ordinal)
                .Select(r => (object)r.ToDict()).ToList(),
            ["assets"] = new List<object>(),
            ["summary"] = new Dictionary<string, object>
            {
                ["assets"] = 0,
                ["preflight_failed"] = Preflight.Count(r => !r.Passed),
            },
        });
    }
}
