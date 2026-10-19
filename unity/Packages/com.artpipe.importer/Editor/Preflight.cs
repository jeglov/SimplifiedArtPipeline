using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace Artpipe
{
    /// <summary>
    /// Checks that the Unity project is set up the way the importer assumes,
    /// before any asset is judged. A failure here is a TOOL error (exit 2),
    /// never an asset failure: the asset can't be blamed for a wrong project.
    /// </summary>
    public static class Preflight
    {
        public static List<CheckResult> Run() => new List<CheckResult>
        {
            ColorSpace(),
            Serialization(),
            RenderPipeline(),
            BuildTarget(),
        };

        // In Gamma space Unity ignores the texture sRGB flag, so the
        // srgb/linear rule per map type in texture_rules.yaml would mean nothing.
        static CheckResult ColorSpace()
        {
            var cs = PlayerSettings.colorSpace;
            return Result("env.color_space", cs == UnityEngine.ColorSpace.Linear,
                new Dictionary<string, object> { ["color_space"] = cs.ToString() },
                cs == UnityEngine.ColorSpace.Linear
                    ? "Project color space is Linear."
                    : $"Project color space is {cs}; the texture rules need Linear (run: artpipe unity setup).");
        }

        // Binary assets can't be diffed or reviewed, and break the "same input,
        // same bytes" promise for anything the importer writes.
        static CheckResult Serialization()
        {
            var mode = EditorSettings.serializationMode;
            bool ok = mode == SerializationMode.ForceText;
            return Result("env.serialization", ok,
                new Dictionary<string, object> { ["mode"] = mode.ToString() },
                ok ? "Assets are serialized as text."
                   : $"Asset serialization is {mode}; it must be ForceText (run: artpipe unity setup).");
        }

        // Material remapping targets URP Lit; under the built-in pipeline the
        // imported materials would render pink.
        static CheckResult RenderPipeline()
        {
            var rp = GraphicsSettings.defaultRenderPipeline;
            string name = rp == null ? "Built-in" : rp.GetType().Name;
            bool ok = name == "UniversalRenderPipelineAsset";
            return Result("env.render_pipeline", ok,
                new Dictionary<string, object> { ["pipeline"] = name },
                ok ? "URP is the active render pipeline."
                   : $"Active render pipeline is {name}; it must be URP (run: artpipe unity setup).");
        }

        // Unity compresses textures for the ACTIVE build target only. The active
        // target lives in Library/, not in ProjectSettings/, so every batchmode
        // run must pass -buildTarget Android or ASTC is never actually produced.
        static CheckResult BuildTarget()
        {
            var target = EditorUserBuildSettings.activeBuildTarget;
            bool ok = target == UnityEditor.BuildTarget.Android;
            return Result("env.build_target", ok,
                new Dictionary<string, object> { ["build_target"] = target.ToString() },
                ok ? "Active build target is Android."
                   : $"Active build target is {target}; run Unity with -buildTarget Android.");
        }

        static CheckResult Result(string id, bool passed, Dictionary<string, object> metrics, string message)
        {
            var r = new CheckResult
            {
                Id = id,
                Passed = passed,
                Severity = Artpipe.Severity.Error,
                Message = message,
                Fixable = false,
            };
            foreach (var kv in metrics) r.Metrics[kv.Key] = kv.Value;
            return r;
        }
    }
}
