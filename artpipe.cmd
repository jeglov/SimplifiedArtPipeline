@echo off
rem artpipe: run pipeline commands under Maya's Python (mayapy).
rem
rem   artpipe build SCENE --class C [--texture T ...]   Maya validate/fix/export, then Unity import
rem   artpipe generate "SUBJECT" --class C --style S --name N   prompt to prefab: concept, image-to-3D, ingest, build
rem   artpipe validate FILE [FILE ...] --class prop_small [--out out] [--max-offenders N]
rem   artpipe fixtures [OUT_DIR]          build the test scenes (default out\fixtures)
rem   artpipe unity-fixtures              rebuild the golden FBX + reports for the Unity tests
rem   artpipe test [PYTEST_ARGS]          run the test suite
rem   artpipe docs [--check]              regenerate the tables in docs\conventions.md
rem   artpipe conventions --dump^|--check  write / verify conventions\conventions.lock.json
rem   artpipe unity setup^|import^|test     run the Unity project headless (pipeline\unity.py)
rem   artpipe gen run WORKFLOW [--param K V]  generate on the pinned ComfyUI, with a provenance manifest
rem   artpipe gen server start^|stop^|status   the pipeline's headless ComfyUI (gen\cli.py)
rem   artpipe gen concept "SUBJECT" --class C --style S [--seed N] [--repair]   concept image + image-to-3D checks
rem   artpipe gen model CONCEPT_RUN --class C [--seed N] [--repair]   image to low-poly GLB + model checks
rem   artpipe gen ingest MODEL_RUN --class C --name N   GLB to a Maya scene + textures
rem   artpipe texture FILE... [--class C]  texture rules in Python, before Unity (validate\texture_cli.py)
rem   artpipe banners setup^|status        pinned renderer tools for UA creatives (banners\cli.py)
rem   artpipe agent setup^|status^|serve    the MCP server for agents (agent\cli.py, agent\server.py)
rem   artpipe queue list^|show^|approve^|reject^|resolve   the approval queue (a human decides)
rem   artpipe py [ARGS]                raw mayapy, e.g. "artpipe py -m pip list"
rem
rem mayapy comes from %MAYAPY% if set, otherwise the newest installed
rem "%ProgramFiles%\Autodesk\Maya20xx". Works from any folder: the repo root
rem is put on PYTHONPATH, and file arguments stay relative to where you are.
rem A .cmd, not a .ps1: Windows blocks .ps1 scripts by default (execution policy).
rem Limit of batch files: an argument can't span several lines (e.g. a multi-line
rem "py -c" script). Write the script to a .py file, or call mayapy directly.

setlocal
rem An MCP client starts the server with a minimal environment (probed: the
rem MCP SDK passes about 12 variables, not ProgramFiles or COMSPEC), and the
rem server runs artpipe.cmd for every tool. Fall back to the defaults.
if not defined ProgramFiles set "ProgramFiles=%SystemDrive%\Program Files"
if not defined COMSPEC set "COMSPEC=%SystemRoot%\System32\cmd.exe"
if not defined MAYAPY (
    for /d %%D in ("%ProgramFiles%\Autodesk\Maya20*") do (
        if exist "%%D\bin\mayapy.exe" set "MAYAPY=%%D\bin\mayapy.exe"
    )
)
if not defined MAYAPY (
    echo artpipe: mayapy not found. Set MAYAPY to the full path of mayapy.exe 1>&2
    exit /b 2
)
if not exist "%MAYAPY%" (
    echo artpipe: MAYAPY points to a missing file: %MAYAPY% 1>&2
    exit /b 2
)

set "ROOT=%~dp0."
set "PYTHONPATH=%ROOT%;%PYTHONPATH%"

set "CMD=%~1"
if "%CMD%"=="" goto usage
shift
set "SUB=%~1"

rem Collect the remaining arguments (shift does not change %*).
set "ARGS="
:collect
if "%~1"=="" goto dispatch
set ARGS=%ARGS% %1
shift
goto collect

:dispatch
if /i "%CMD%"=="build" "%MAYAPY%" -m pipeline.build %ARGS% & goto done
if /i "%CMD%"=="generate" "%MAYAPY%" -m pipeline.generate %ARGS% & goto done
if /i "%CMD%"=="validate" "%MAYAPY%" -m validate.maya.cli %ARGS% & goto done
if /i "%CMD%"=="fixtures" "%MAYAPY%" "%ROOT%\tests\fixtures\broken_meshes.py" %ARGS% & goto done
if /i "%CMD%"=="unity-fixtures" "%MAYAPY%" "%ROOT%\tests\fixtures\unity_fixtures.py" %ARGS% & goto done
if /i "%CMD%"=="docs" "%MAYAPY%" -m validate.maya.gen_docs %ARGS% & goto done
if /i "%CMD%"=="conventions" "%MAYAPY%" -m validate.core.lock %ARGS% & goto done
if /i "%CMD%"=="unity" "%MAYAPY%" -m pipeline.unity %ARGS% & goto done
if /i "%CMD%"=="gen" "%MAYAPY%" -m gen.cli %ARGS% & goto done
if /i "%CMD%"=="texture" "%MAYAPY%" -m validate.texture_cli %ARGS% & goto done
if /i "%CMD%"=="banners" "%MAYAPY%" -m banners.cli %ARGS% & goto done
if /i "%CMD%"=="agent" if /i "%SUB%"=="serve" goto agent_serve
if /i "%CMD%"=="agent" "%MAYAPY%" -m agent.cli %ARGS% & goto done
if /i "%CMD%"=="queue" "%MAYAPY%" -m agent.queue_cli %ARGS% & goto done
if /i "%CMD%"=="test" "%MAYAPY%" -m pytest -q "%ROOT%\tests" %ARGS% & goto done
if /i "%CMD%"=="py" "%MAYAPY%" %ARGS% & goto done
echo artpipe: unknown command "%CMD%" 1>&2

:usage
echo usage: artpipe build^|generate^|gen^|texture^|banners^|agent^|queue^|validate^|fixtures^|unity-fixtures^|test^|docs^|conventions^|unity^|py [ARGS]   (see the header of artpipe.cmd) 1>&2
exit /b 2

:agent_serve
rem The MCP server runs in its own venv (Python 3.12, `artpipe agent setup`), not
rem under mayapy. stdout is the protocol: nothing else may print to it.
set "AGENT_TOOLS=%ARTPIPE_TOOLS%"
if not defined AGENT_TOOLS set "AGENT_TOOLS=%LOCALAPPDATA%\artpipe"
if not exist "%AGENT_TOOLS%\agent-venv\Scripts\python.exe" (
    echo artpipe: the agent venv is missing: run "artpipe agent setup" 1>&2
    exit /b 2
)
"%AGENT_TOOLS%\agent-venv\Scripts\python.exe" -m agent.server & goto done

:done
exit /b %ERRORLEVEL%
