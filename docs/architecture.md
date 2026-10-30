# WebUI architecture

Version 5 keeps all game processing in Python and replaces the primary Tk interface with a local WebUI.

## Runtime

- `app.py` and `app.pyw` launch `vc_extractor.webui.run`.
- pywebview opens the bundled `vc_extractor/webui_assets/index.html` in a native Windows window.
- The frontend calls the narrow `WebUIApi` bridge for file dialogs, ROM management, metadata, capture, artwork rendering, repository search, previews, and boot sounds.
- Native window and parent-controller references use private fields. pywebview recursively inspects public bridge attributes, so native objects must never be stored as public API state.
- ROM data and imported media are not sent to a hosted application service. Optional metadata, artwork, and sound repository requests continue to use the providers documented in the application.
- Microsoft Edge WebView2 renders the interface on Windows.
- Selected Preview uses the original two-by-two output order and can be detached as one synchronized panel. Individual enlarged previews reuse one fit-to-window child window.
- The main workspace gives Selected Game about two-thirds of the available width by default, with a draggable divider for rebalancing Selected Output.
- The application shell is constrained to the WebView viewport, with relative-height sections and no page-level scrollbar. ROM rows, tab content, narrow stacked workspaces, and Activity messages scroll only inside their own regions.
- Adjust Crop and Zoom uses a compact left-side dialog with no dimming or backdrop blur and serializes live rebuilds so rapid slider movement keeps only the latest pending change.
- Individual enlarged previews open near the usable screen size and may upscale small sources to fill the available image stage.
- Detaching Activity removes the embedded panel until the detached window is closed or reattached.

## Development run

On Windows, run `Setup_and_Run.bat`. It creates a local virtual environment, installs the pinned requirements, and launches the WebUI without a console window. After setup, `Run.bat` starts it directly.

## Portable build

Run `Build_Windows_EXE.bat` for one local executable, or run the Sharing Kit's `Build_Portable_Release.bat` to create the complete portable folder and ZIP. Both scripts bundle the HTML, CSS, JavaScript, templates, icon assets, original generic sounds, pywebview runtime, and miniaudio decoder.

## Main source files

- `vc_extractor/webui.py`: application controller and native window lifecycle.
- `vc_extractor/models.py`: UI-independent game-job state.
- `vc_extractor/webui_assets/index.html`: main workspace structure.
- `vc_extractor/webui_assets/style.css`: responsive desktop styling.
- `vc_extractor/webui_assets/app.js`: rendering and interaction layer.
- `vc_extractor/webui_assets/preview_window.*`: detached image preview.
- `vc_extractor/webui_assets/activity_window.*`: detached live activity stream.

The prior `vc_extractor/gui.py` module remains in the source snapshot as a compatibility reference for the established processing workflows, but it is not the application entry point.
