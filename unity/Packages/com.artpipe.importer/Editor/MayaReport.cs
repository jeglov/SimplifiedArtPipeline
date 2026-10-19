using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Artpipe
{
    /// <summary>A Maya report that can't be trusted as written (unreadable, unknown version, wrong shape).</summary>
    public sealed class ReportException : Exception
    {
        public ReportException(string message) : base(message) { }
    }

    /// <summary>
    /// Typed, strict view of a Maya validation report
    /// (contract: conventions/report.schema.json). Every key must be present,
    /// unknown keys are rejected, and only known schema versions are read.
    /// </summary>
    public sealed class MayaReport
    {
        public static readonly string[] SupportedSchemaVersions = { "1.2" };

        public string SchemaVersion { get; set; }
        public string Tool { get; set; }
        public FileRef Source { get; set; }
        public string AssetClass { get; set; }
        public string Status { get; set; }
        [JsonNullable] public string ToolError { get; set; }
        public ReportSummary Summary { get; set; }
        public List<MayaCheckResult> Results { get; set; }
        public List<MayaFixResult> Fixes { get; set; }
        [JsonNullable] public FileRef FixedFrom { get; set; }
        [JsonNullable] public FileRef ExportedFrom { get; set; }
        [JsonNullable] public ExportInfo Export { get; set; }

        public static MayaReport Parse(string json)
        {
            JObject doc;
            try
            {
                doc = JObject.Parse(json);
            }
            catch (JsonException e)
            {
                throw new ReportException("not valid JSON: " + e.Message);
            }
            // Version first: a newer report has keys this code doesn't know,
            // and "unknown key" would hide the real problem.
            var version = (string)doc["schema_version"];
            if (Array.IndexOf(SupportedSchemaVersions, version) < 0)
                throw new ReportException(
                    $"schema_version {version ?? "(missing)"} is not supported (reads {string.Join(", ", SupportedSchemaVersions)})");
            try
            {
                return doc.ToObject<MayaReport>(StrictJson.Serializer());
            }
            catch (JsonException e)
            {
                throw new ReportException("does not match the report contract: " + e.Message);
            }
        }
    }

    public sealed class FileRef
    {
        public string Path { get; set; }
        public string Sha256 { get; set; }
    }

    public sealed class ReportSummary
    {
        public int Total { get; set; }
        public int Passed { get; set; }
        public int Errors { get; set; }
        public int Warnings { get; set; }
        public int Fixable { get; set; }
        public int FixesApplied { get; set; }
    }

    public sealed class MayaCheckResult
    {
        public string Id { get; set; }
        public bool Passed { get; set; }
        public string Severity { get; set; }
        public string Message { get; set; }
        public bool Fixable { get; set; }
        /// <summary>Free-form per check, so kept as JSON.</summary>
        public JObject Metrics { get; set; }
        public List<string> Offenders { get; set; }
        public int OffenderCount { get; set; }
    }

    public sealed class MayaFixResult
    {
        public string Id { get; set; }
        public bool Applied { get; set; }
        public string Message { get; set; }
        public List<string> Changed { get; set; }
        public int ChangedCount { get; set; }
    }

    public sealed class ExportInfo
    {
        public string Format { get; set; }
        public int SettingsVersion { get; set; }
        public string FileVersion { get; set; }
        public string UpAxis { get; set; }
        public double UnitScaleCm { get; set; }
    }
}
