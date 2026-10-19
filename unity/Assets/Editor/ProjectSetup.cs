using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

/// <summary>
/// One-time, idempotent setup of the host project: URP pipeline asset, Linear
/// color space, text serialization. Run with <c>artpipe unity setup</c>; the
/// results are committed under Assets/Settings and ProjectSettings/.
///
/// Lives in the project, not in com.artpipe.importer: a real game project
/// already has its own pipeline settings, and the importer package must not
/// overwrite them. The package only checks them (Artpipe.Preflight).
/// </summary>
public static class ProjectSetup
{
    const string Folder = "Assets/Settings";
    const string RendererPath = Folder + "/URP_Mobile_Renderer.asset";
    const string PipelinePath = Folder + "/URP_Mobile.asset";

    public static void Apply()
    {
        int code = 0;
        try
        {
            EditorSettings.serializationMode = SerializationMode.ForceText;
            PlayerSettings.colorSpace = ColorSpace.Linear;

            var pipeline = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(PipelinePath);
            if (pipeline == null)
            {
                Directory.CreateDirectory(Folder);
                var renderer = ScriptableObject.CreateInstance<UniversalRendererData>();
                AssetDatabase.CreateAsset(renderer, RendererPath);
                pipeline = UniversalRenderPipelineAsset.Create(renderer);
                AssetDatabase.CreateAsset(pipeline, PipelinePath);
            }
            GraphicsSettings.defaultRenderPipeline = pipeline;
            // A quality level can override the pipeline. Clear every override so
            // the project has exactly one pipeline, whatever level is active.
            int current = QualitySettings.GetQualityLevel();
            for (int i = 0; i < QualitySettings.names.Length; i++)
            {
                QualitySettings.SetQualityLevel(i, false);
                QualitySettings.renderPipeline = null;
            }
            QualitySettings.SetQualityLevel(current, false);

            AssetDatabase.SaveAssets();
            Debug.Log("artpipe: project setup applied");
        }
        catch (System.Exception e)
        {
            Debug.LogError("artpipe: project setup failed: " + e);
            code = 2;
        }
        EditorApplication.Exit(code);
    }
}
