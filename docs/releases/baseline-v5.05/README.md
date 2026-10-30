# Wii U VC Image Builder v5.05 WebUI

Wii U VC Image Builder creates Wii U Virtual Console artwork from ROM captures, imported title-screen screenshots, and optional community artwork. Version 5 presents the existing Python processing engine through a local HTML, CSS, and JavaScript workspace in a native Windows window. ROMs and media remain on the user's computer.

The Windows interface uses Microsoft Edge WebView2, which is included with current Windows 10 and Windows 11 installations. The provided setup and build scripts install the pinned pywebview Python package automatically.

## Two workflows

### ROM capture
1. Add a ROM or ROM folder.
2. Metadata lookup starts immediately using the public Libretro Database.
3. metadata.txt is written immediately from detected ROM information and refreshed as soon as the database lookup completes.
4. Select the ROM and choose Capture ROM Screens.
5. The application captures candidates, automatically isolates the correct Nintendo DS screen when applicable, selects the best title screen, and builds the artwork.

### Existing screenshot
1. Add and select the corresponding ROM.
2. Choose Import Screenshot.
3. Select an existing PNG/JPG/BMP/WebP/TGA title-screen image.
4. Edit the detected name, console metadata, product code, or region if needed.
5. The application builds the artwork immediately without launching RetroArch or Dolphin.

## Generated files

- title_screen.png - selected or imported title screen
- Icon Image.png - screenshot-only 128 x 128 icon input, with no extra VC frame
- TV Image.png - 1280 x 720 banner built with the bundled UWUVCI template
- Gamepad Image.png - 854 x 480 GamePad banner
- metadata.txt - metadata for reference/manual entry
- bootSound.wav - optional launch sound, automatically converted from a selected WAV, MP3, or BTSND source

## Metadata

ROM metadata is fetched automatically when ROMs are added, not when capture starts. No account or API key is required. Libretro results are cached locally. The Fetch Metadata button remains available as a manual refresh/retry.

Imported screenshots remain attached to the selected ROM, so hash-based metadata and manual corrections stay associated with the correct game.

## Repository artwork

Multi-source artwork and boot-sound search is enabled by default. The application searches exact product-code art in UWUVCI Community Images first and the original Cucholix WiiVC BIS repository second. When exact art is absent, it can search the GameCube Inject Artwork collection and the official per-console Libretro Thumbnails repositories using metadata title, internal ROM title, filename title, and region evidence. Every supported console has a Libretro repository mapping.

Downloaded iconTex and boot image artwork can be used directly for Icon Image.png, TV Image.png, and Gamepad Image.png. Libretro title screens, screenshots, box art, or logos are passed through the normal renderer. The source repository, match method, matched title, artwork type, confidence, and product code are recorded in metadata.txt. Use Settings to configure the search or open its cache. The Use Generated Artwork command in the Game tab restores the previously captured or imported source.

Downloaded Libretro source images respond immediately to framing, generic Automatic/Full/Top/Bottom/Left/Right source-area choice, icon-frame, template-placement, and metadata-overlay changes. Capturing a new screen or importing a screenshot makes that image active and prevents automatic artwork lookup from silently replacing it again; the downloaded file remains cached and can be selected again with a manual artwork search.

When several artwork matches exist across repositories or Libretro artwork types, they are retained together and can be previewed and selected in Artwork Match Review. A visible Review Artwork Matches button in the Game tab shows the retained count. Each match can be assigned to all available outputs, only Icon Image, or only the paired TV/Gamepad Images, allowing different repository images to be combined for one game. The review list and details identify the current assignment for each output group. Lower-confidence but decodable images can be included for manual review and are never applied automatically. Clicking any main preview image opens a separate, larger resizable quick-review window.

Image-file usability and title confidence are separate. A downloaded file must use a supported image extension, remain under the 24 MB safety limit, and decode into image pixels. If a Libretro raw download cannot be decoded, the application retries through GitHub's media endpoint and skips only that artwork type if the retry also fails, preserving other valid images for review.

## Boot sounds

The automatic source pass searches Cucholix WiiVC BIS for an exact per-game boot sound and falls back to a distinct startup sound for that console when one exists. GameCube can also use the standard generic WAV files from GameCube Inject Artwork. Downloaded defaults are labeled by console. The selected game's Boot Sound panel lists all retained results and supports manual WAV, MP3, or BTSND selection. Set Console imports a reusable system-specific startup sound for any supported console and applies it to current and future games unless a per-game or manual override is active. Every supported source is converted automatically to bootSound.wav.

The selector contains two original offline choices: Generic Retro Startup and Generic Modern Startup. More Sounds opens a system-specific MyInstants search in the default browser; a permitted download can then be imported with Browse for one game or Set Console for an entire system. The application does not scrape or redistribute MyInstants-hosted audio.

Browse converts an imported per-game sound to WAV and stores a private working copy beneath game_boot_sounds in the active configuration directory. The saved choice is restored automatically when the same ROM is added in a later session. Clearing an active stored per-game sound also removes that persistent copy. Set Console continues to store reusable system-wide choices beneath console_boot_sounds.

Play Boot Sound now stops any prior asynchronous preview, refreshes the conversion from the active selection, and plays a unique temporary copy. The status and activity log identify the exact manual, per-game, downloaded console, or local console source being played, preventing an older console default from continuing under the reused bootSound.wav filename.

Exact product codes remain highest priority. Confident title matches can be applied automatically. Possible lower-confidence matches are retained only when the manual-review setting is enabled and require the user to choose them explicitly. Review each source's terms and the rights applicable to an image before redistributing downloaded art.

## Templates and typography

The program includes the UWUVCI Virtual Console template images used for the supported systems. Open Settings, Artwork Font to select an existing UWUVCI font.otf or another OTF/TTF file, return to the automatic/default font, or review the active selection. If a custom font is unavailable, the program follows UWUVCI's Trebuchet MS fallback behavior.

## Stopping capture

Stop and Use Captures closes the current emulator session, analyzes the frames captured so far, exports the best available artwork, and keeps the candidate frames available in Review Selected.


Version 5.05 changes

- Fits the complete application shell within the native window at launch without a page-level vertical or horizontal scrollbar.
- Sizes the header, ROM list, workspace, and Activity panel relative to the available viewport height.
- Keeps overflow local to the ROM table, selected-game tabs, narrow workspace, and Activity log so their controls remain reachable without moving the entire application.
- Preserves the resizable ROM, Selected Game, Selected Output, and Activity regions, including detached preview and Activity windows.
- Adds a compact height-responsive layout for smaller windows and Windows display scaling.


Version 5.04 changes

- Removes all dimming and backdrop blur from Adjust Image Crop and Zoom.
- Keeps the Selected Output previews fully clear and visible behind the compact left-side framing controls.


Version 5.03 changes

- Expands the Selected Game panel to roughly two-thirds of the workspace by default while keeping the Selected Output panel visible and resizable.
- Uses a compact left-side Adjust Image Crop and Zoom dialog so the output previews remain visible behind it.
- Coalesces rapid slider changes so only the latest pending live rebuild runs instead of allowing a queue of obsolete image rebuilds.
- Opens clicked image previews near the usable screen size and lets low-resolution images scale to fill the available preview area.
- Retains the single reusable enlarged window and automatically refits the active image whenever that window is resized.


Version 5.02 changes

- Restores the original two-by-two preview order: Title Screen, Icon Image, TV Image, and Gamepad Image.
- Restores Detach Preview for moving the complete live preview panel into a separate resizable window.
- Adds Reattach Preview and Adjust Crop and Zoom controls to the detached preview panel.
- Reuses one enlarged image window and fits its image to the available screen and every later window size.
- Hides the embedded Activity panel while its detached window is open and restores it when reattached or closed.


Version 5.01 changes

- Fixes the immediate startup freeze caused by exposing the native pywebview Window through the public JavaScript API bridge.
- Keeps native window and parent-controller references private so pywebview exposes only the intended application methods.
- Applies the same bridge-safety correction to detached image-preview and Activity windows.
- Adds regression coverage that rejects recursive native objects in every WebUI bridge.


Version 5.00 changes

- Replaces the Tk desktop interface with a local responsive WebUI hosted inside a native pywebview window.
- Keeps ROM processing, metadata, capture, artwork rendering, repository search, and audio conversion in the existing Python engine.
- Adds a compact project workspace with a resizable ROM table, tabbed controls, live image previews, operation status, and activity stream.
- Opens preview images and the Activity stream in independently resizable native windows.
- Preserves drag and drop, multi-row checking/removal, artwork-match review, separate Icon and TV/Gamepad assignments, capture review, generic source-area controls, and persistent boot sounds.
- Packages the WebUI files and pywebview runtime through the Windows EXE and portable-release build scripts.
- Keeps all ROMs and working media local. No hosted service or upload step is introduced.


Version 4.53 changes

- Removes all eight MyInstants-derived console recordings from the application package and Windows Sharing Kit.
- Keeps only the two original generic retro and modern startup chimes as offline built-in sounds.
- Stores per-game sounds imported with Browse beneath game_boot_sounds in the active configuration directory.
- Restores a stored per-game sound automatically when the same ROM is added in a later session.
- Removes the persistent per-game copy when that active stored sound is cleared.
- Retains console-specific MyInstants browser searches and the reusable Set Console workflow without redistributing site-hosted audio.


Version 4.52 changes

- Bundles eight supplied console startup recordings as offline choices for Game Boy, Game Boy Color, Game Boy Advance SP, Nintendo 64, Nintendo DS, GameCube, Wii, and WiiWare.
- Adds standard and alternate four-controller GameCube startup choices.
- Adds original retro and modern generic startup chimes for consoles without a supplied recording.
- Adds More Sounds to open a console-specific MyInstants search, with downloaded files imported through Browse or Set Console.
- Keeps a user-selected built-in sound active when repository lookup returns other candidates.
- Packages all built-in sound assets into the portable Windows executable.
- The eight site-derived recordings introduced in this version were removed from v4.53 after their MyInstants provenance was confirmed.


Version 4.51 changes

- Adds separate Use for Icon Image and Use for TV/Gamepad Images actions to Artwork Match Review.
- Keeps Use for All Outputs as the one-click choice for assigning one match everywhere it is available.
- Preserves the other output group's current artwork when a match is assigned to only Icon or TV/Gamepad.
- Shows Icon and TV/Gamepad assignment markers in the retained-match list and details panel.
- Records separate repository artwork assignments in metadata.txt and reapplies them during live rebuilds.
- Fixes manual boot-sound preview so Play always uses the active file rather than a previously playing console default.
- Labels downloaded generic sounds by console and keeps them distinct from per-game choices.
- Adds Set Console for storing a reusable system-specific startup sound for every supported console.
- Automatically uses a configured local console sound for future games while preserving manual and per-game overrides.


Version 4.50 changes

- Converts selected WAV, MP3, and BTSND boot sounds automatically to bootSound.wav.
- Adds Play Boot Sound and Stop controls to the per-game Boot Sound panel.
- Adds a visible Review Artwork Matches button and retained-match count beside Selected Preview.
- Adds Include Possible Matches for Manual Review so decodable low-confidence results can be reviewed without being applied automatically.
- Retries unreadable Libretro raw responses through GitHub's media endpoint.
- Skips only an unreadable artwork type while preserving other valid images found for the same game.


Version 4.49 changes

- Clicking Title Screen, Icon Image, TV Image, or Gamepad Image opens a larger independently resizable preview.
- Artwork search now retains multiple safe matches across providers and Libretro artwork types for visual review and selection.
- Added Artwork Match Review and a File-menu command to revisit retained matches.
- Replaced the Nintendo DS-only source selector with Automatic, Full Image, Top, Bottom, Left, and Right controls available for every game and console.
- Added automatic per-game boot-sound discovery with a generic console fallback when repository files exist.
- Added GameCube generic boot-sound choices from GameCube Inject Artwork.
- Added a per-game Boot Sound selector with online search, manual WAV/MP3/BTSND selection, and Clear.
- Selected audio is converted to bootSound.wav and documented in metadata.txt.


Version 4.48 changes

- Added Automatic, Full Image, Top Screen, and Bottom Screen controls for Nintendo DS source artwork in Adjust Image Framing.
- Applies the Nintendo DS screen choice consistently to title_screen.png, Icon Image.png, TV Image.png, and Gamepad Image.png.
- Downloaded Libretro source artwork now rebuilds immediately when framing, icon-frame, template-placement, or metadata-overlay settings change.
- Removed the need to refetch metadata before downloaded source artwork reflects current settings.
- New emulator captures and explicitly imported screenshots now replace downloaded artwork as the active source.
- Automatic artwork lookup will not silently reapply a cached download after the user chooses a capture or imported screenshot; manual artwork search remains available.


Version 4.47 changes

- Added Libretro Thumbnails as an enabled-by-default artwork fallback for every supported console.
- Uses title-screen images first, followed by gameplay screenshots, box art, and logos when available.
- Added metadata-title, internal-title, filename-title, and region fallbacks when exact product-code artwork is unavailable.
- Added confidence and ambiguity safeguards for approximate matches.
- Added the GameCube Inject Artwork collection as a GameCube-specific finished-art source.
- Added individual source and title-fallback controls under Settings, Repository Artwork.
- Libretro source images pass through the normal icon, TV, and Gamepad rendering and framing pipeline.
- metadata.txt now records the artwork source, match method, matched title, artwork type, confidence, and product code.


Version 4.46 changes

- Added automatic exact product-code artwork lookup, enabled by default and configurable under Settings, Repository Artwork.
- Searches UWUVCI Community Images first and the original Cucholix WiiVC BIS collection as a fallback.
- Added manual Download Repository Artwork and Use Generated Artwork commands to the File menu.
- Repository icon and boot artwork are converted to the application's standard Icon, TV, and Gamepad output files.
- Downloads are cached beneath the active configuration folder and can be opened from Settings.
- metadata.txt records the repository source, URL, and exact matched product code.
- No community artwork is bundled in the sharing kit, and no fuzzy title matching is performed.


Version 4.45 changes

- Added native drag and drop for supported ROM files and folders.
- ROM files and folders can be dropped anywhere on the main application window or directly on the ROM list.
- Dropped folders are scanned recursively using the same supported-extension rules as Add ROM Folder.
- Dropped ROMs use the existing duplicate protection, validation, metadata creation, and automatic lookup workflow.
- Added the TkinterDnD2 runtime and PyInstaller hook so drag and drop remains available in the standalone Windows build.


Version 4.44 changes

- Changed Check Selected into a state-aware Check Selected and Uncheck Selected toggle.
- When every highlighted ROM is checked, the button displays Uncheck Selected and clears those checks.
- When any highlighted ROM is unchecked, the button displays Check Selected and checks all highlighted ROMs.
- The toggle is disabled when no ROM rows are highlighted.


Version 4.43 changes

- Removed the Select All and Select None row-highlight controls from above the ROM list.
- Added Remove Checked beside the existing Capture checkbox controls.
- Remove Checked removes only ROMs whose Capture boxes are checked. Highlighted but unchecked rows are not removed.
- The Remove Checked button is enabled only when at least one removable ROM is checked.
- Delete now runs Remove Checked when the ROM list has focus.


Version 4.42 changes

- Removed the oversized Remove button from the main toolbar.
- Added a compact row-action bar directly above the ROM list with Select All, Select None, and Remove Selected.
- Remove Selected operates only on highlighted rows; Capture checkboxes remain dedicated to capture processing.
- The Remove Selected button is disabled until at least one row is highlighted.
- Added standard ROM-list shortcuts: Ctrl+A selects all rows, Escape clears selection, and Delete removes highlighted rows.
- The per-row X remains available for removing one entry directly.


Version 4.41 changes

- Added a compact Remove control to every ROM-list row beside the Capture checkbox.
- Clicking a row's Remove control removes only that entry from the application list and never deletes the source ROM.
- The existing toolbar Remove command remains available for removing multiple highlighted rows.
- Removal is temporarily blocked while capture or metadata processing is active, preventing job-index conflicts.
- Row selection is preserved when possible after a removal, and rapid double-clicks cannot accidentally remove consecutive rows.


Version 4.40 changes

- Replaced the Activity Logging submenu with a dedicated settings window containing an enable checkbox, editable output directory, Browse, Open Folder, Save, and Cancel controls.
- Logging changes are applied only when Save is selected.
- Added a File menu with Add ROMs, Add ROM Folder, screenshot import, custom TV/Gamepad image import, selected output and metadata access, and Exit.
- Added Ctrl+O and Ctrl+Shift+O shortcuts for adding ROM files and folders.


Version 4.39 changes

- Restored a compact description at the top of Capture Settings so the panel retains useful context without consuming substantial workspace.
- Detailed control explanations remain available through hover help and the built-in Documentation Center.


Version 4.38 changes

- Capture Settings now has a permanent vertical scrollbar and supports mouse-wheel scrolling inside the panel, so its controls remain reachable at practical window heights.
- ROM-list commands now occupy a protected row beneath the scrolling table and remain reachable when the ROM pane is resized.
- The main toolbar now uses four responsive columns to preserve complete button access at the minimum supported window width.
- Window and pane minimums prevent resize bars from collapsing required controls out of reach.
- Removed redundant instructional paragraphs from the main workspace while retaining detailed hover help and built-in documentation.
- Moved artwork font selection from the main page to Settings, Artwork Font, with commands to select, reset, and inspect the configured font.
- Preview tiles use smaller minimum heights so they scale more naturally without hiding their panel controls.


Version 4.37 changes

- Main-window autosizing now runs only once during startup. Selecting a ROM or refreshing artwork no longer resets a user-selected window size or position.
- Added visible grab bars between the ROM list and lower workspace, Capture Settings and Selected Preview, and the main workspace and Activity.
- Added horizontal and vertical scrollbars to the ROM list.
- Converted the main toolbar to flexible rows that adapt to practical window widths.
- Reduced preview-grid minimum sizes so preview tiles scale more naturally with the window.
- Explanatory text now wraps according to the width currently available in its panel.


Version 4.36 changes

- Rebuilt Activity as a separately resizable lower pane with a draggable divider.
- Added Close, Detach, and Reattach controls for Activity, plus View menu commands to restore or detach it.
- Activity messages remain buffered while the panel is closed and return when it is shown again.
- Added configurable live Activity file logging under Settings, Activity Logging.
- Selecting a log output folder enables live logging by default; logging can be disabled without forgetting the folder or deleting existing logs.
- Each session writes immediately to its own timestamped UTF-8 log file.
- Removed the Configuration folder selector from the main page. Configuration-folder controls remain in the Settings menu.


## v3.9 screenshot-import workflow

Manual screenshots no longer create standalone games. Add the ROM first. The application immediately creates header-derived metadata.txt, starts the public Libretro lookup, and refreshes metadata.txt when the lookup completes.

To bypass emulator capture, select the loaded ROM and click Import Screenshot for Selected Game. The imported image is attached to that ROM entry and uses the ROM-derived game name, console, product code, region, release year, players, developer, and publisher. The program immediately builds title_screen.png, Icon Image.png, TV Image.png, and Gamepad Image.png from the imported screenshot without launching RetroArch or Dolphin.


Version 3.4 changes
- Added Select All and Select None controls for Icon Image, TV Image, and Gamepad Image.
- Added per-game Screenshot Placement / Size controls using X, Y, Width, and Height with console-default reset.
- Added GameTDB as an automatic no-account metadata fallback for supported systems when Libretro is missing release year or player count.


Version 3.4 changes
- simplified the metadata panel
- fixed Select None clipping
- invalidates older partial metadata cache records and refreshes cached metadata periodically
- incomplete cached metadata is re-enriched from current Libretro release-year and max-user files
- adds a conservative title-based metadata fallback when an exact optional CRC record is absent


Version 3.5 changes

- Icon Image now uses the bundled 128 x 128 UWUVCI-style icon border when a matching template exists
- the selected screenshot is cropped to fill the designated placeholder area inside that icon border


Version 3.6 changes

- Added an Icon Image frame selector for the selected game
- The selector only shows built-in icon borders relevant to that game's system
- Changing the frame for a captured or imported game immediately rebuilds the Icon Image


Version 3.7 changes

- Game Boy and Game Boy Color can now use installed mGBA or VBA-M RetroArch cores
- dedicated Gambatte and SameBoy cores remain supported fallbacks


Version 3.8 changes

- Game Boy and Game Boy Color now use the same GBA-capable RetroArch cores as Game Boy Advance
- Added live previews for Title Screen, Icon Image, TV Image, and Gamepad Image in the main window


Version 3.9 changes

- Main window automatically sizes itself to the complete live preview layout while staying within the desktop
- Screenshot only is now a distinct icon mode and no longer falls back to Frame 1
- Screenshot-only icons preserve the processed screenshot aspect ratio with black letterboxing when necessary


Version 4.1 changes

- TV Image and Gamepad Image no longer stretch title screenshots to the template opening
- banner screenshots preserve their original aspect ratio, scale uniformly to cover the opening, and crop only the excess
- Gamepad Image is rendered directly from the original screenshot instead of shrinking the completed TV Image


Version 4.2 changes

- Wii ISO detection using the native Wii disc header
- Wii ISO and WBFS files are routed through Dolphin
- WiiWare WAD files can be loaded and routed through Dolphin
- Added Wii and WiiWare banner-template selection
- Added MSX ROM and TurboGrafx-CD CUE loading for systems that already have bundled templates
- Wii metadata prefers the disc ID through GameTDB instead of hashing the entire disc image


Version 4.3 changes

- GameCube, Wii, and WiiWare now prefer RetroArch Dolphin core capture
- Standalone Dolphin.exe is optional and used only as a fallback
- Added dolphin_libretro.dll core discovery for GameCube, Wii, and WiiWare


Version 4.4 changes

- Normal timed capture completion now uses the same explicit finalization/export path as Stop and Use Captures
- Export completion is verified before a game is marked Complete
- Nintendo DS worker finalization no longer reads Tk variables from a background thread


Version 4.5 changes

- Added a Dolphin libretro preflight check for the required RetroArch system/dolphin-emu/Sys files
- Gives a clear setup message instead of opening a black Dolphin window when those files are missing
- Dolphin capture sessions temporarily use RetroArch OpenGL for compatibility without changing the user's permanent RetroArch settings


Version 4.6 changes

- GameCube, Wii, and WiiWare automatically fall back to external Dolphin.exe when the RetroArch Dolphin core is unavailable, incomplete, errors during capture, or produces only black frames.
- Metadata lookup remains independent of the emulator used for capture.


Version 4.8 changes

- Removed the redundant nested Live Preview frame. The single Selected Preview panel contains all four generated-image previews.
- Selected Preview can be detached into its own window and reattached at any time. Closing the detached preview window reattaches it automatically.
- Expanded metadata matching across every supported system using source-appropriate Libretro DAT candidates, normalized cartridge hashes, product codes, hashes, and conservative title matching.
- NES metadata now handles iNES/headerless hash differences.
- SNES metadata now handles 512-byte copier-header differences.
- Nintendo 64 metadata also recognizes common byte-order variants.
- Added libretro-dats, Redump, and TOSEC identification fallbacks where applicable, while retaining GameTDB for Wii, WiiWare, and GameCube/DS supplementation.

Version 4.10 changes

- Wii and WiiWare banner text is now rendered in hard-separated safe zones above and below the screenshot.
- Title and release/player text layers are clipped to their own zones, so text stroke/glow cannot overlap the game image.
- Wii screenshot space expands into unused title or metadata bands when those individual overlay items are disabled.


Version 4.12 changes

- Increased hard separation between Wii/WiiWare title, screenshot, and metadata zones so text cannot touch the screenshot.
- Replaced generic Dolphin image searching with direct extraction of the matching Wii save banner.bin used by Dolphin.
- Dolphin banner lookup now requires the matching game save ID or an exact banner title match and never switches sources on failure.
- Added a visible Wii TV/Gamepad source selector and renamed the restore action to Use Screenshot for TV/Gamepad.
- Imported image, Dolphin banner, and selected screenshot sources can be switched without losing the original selected screenshot.


Version 4.13 changes

- Removed Dolphin banner import/extraction for Wii artwork because it was not reliably selecting the same assets shown by UWUVCI/Dolphin.
- Wii/WiiWare TV and Gamepad source is now intentionally limited to Selected Screenshot or Custom Image.
- Renamed the restore action to Use Selected Screenshot for TV/Gamepad.
- External Dolphin.exe remains available as an emulator capture fallback; only the artwork-import feature was removed.


Version 4.14 changes

- Wii/WiiWare metadata text is rendered entirely on the opaque frame above and below the unchanged full screenshot opening.
- Added independent Image Crop / Position controls for Icon, TV, and Gamepad outputs.
- Each output can shift its cover crop horizontally and vertically from 0 through 100 while preserving aspect ratio.
- Wii TV/Gamepad source remains limited to Selected Screenshot or Custom Image.


Version 4.15 changes
- Image Crop / Position updates generated previews automatically after slider changes using a short debounce.
- Apply now refreshes the image without closing the crop-position window; Done saves and closes.
- Added Link TV and Gamepad positions so their horizontal and vertical crop-focus controls can move together.
- Cancel restores the crop positions that were active when the dialog was opened.


Version 4.17 changes
--------------------
- Crop-position controls renamed to Image Crop & Zoom and now include independent zoom controls for Icon, TV, and Gamepad outputs.
- TV and Gamepad position and zoom can be linked.
- Crop/zoom controls remain available when the Selected Preview is detached.
- Review Selected now has a Done button and re-export no longer opens a blocking confirmation dialog.


Version 4.18 changes
--------------------
- Removed Cemu configuration and Wii U game-input support. The application remains a Wii U Virtual Console artwork builder, but Wii U-native games are no longer treated as source titles.
- Removed WUD, WUX, WUA, and RPX from supported input extensions.
- When Selected Preview is detached, Image Crop & Zoom now opens as a child of the detached preview window instead of the main application window.
- Live crop/zoom refreshes no longer resize or raise the main application window while the preview is detached.

Version 4.19 changes
- Renamed legacy alternate icon frames to Light Frame and Dark Frame to match the bundled artwork.
- Renamed Wii icon choices to WiiWare Frame and Wii Frame.
- Wii games now default to the Wii Frame; WiiWare defaults to the WiiWare Frame.
- Product Code editing is now part of Edit Game Details; the separate Edit Product Code toolbar button was removed.

Version 4.20 changes
- Detached Selected Preview now keeps its current user-selected size and position during live crop, position, and zoom refreshes.
- The detached preview is auto-sized only when it is first opened. Subsequent image rebuilds no longer recalculate its geometry.


Version 4.23 changes
--------------------
- Added a standard Help menu to the main application.
- Added a built-in Documentation Center with Getting Started, workflow, artwork/framing, game-details, metadata, emulator setup, supported systems/files, output, troubleshooting, and credits/notices topics.
- F1 opens the Documentation Center.
- Added an About dialog with version information and third-party notices.
- Documentation is bundled directly with portable builds and does not require external help files.


Version 4.24 changes
--------------------
- Added a persistent screenshot-capture requirement notice to the main window.
- The notice explains that automatic ROM screenshot capture requires RetroArch and may use Dolphin for GameCube/Wii titles.
- Clarified directly in the main UI that manually importing an existing screenshot does not require an emulator.
- Added a Capture Setup Help button that opens the built-in Emulator Capture Setup documentation.


Version 4.35 changes

- Hover help now closes automatically after a readable interval instead of remaining indefinitely.
- Tooltips close when the pointer moves meaningfully, the user clicks, scrolls, types, changes focus, or leaves the control.
- A dismissed tooltip stays hidden until the pointer leaves and re-enters its control.
- Pointer movement resets the initial hover delay so help appears only after the pointer settles.

Version 4.34 changes

- Added a checkbox-style Capture selector to every loaded ROM.
- Added Check Selected, Check All, and Clear Checks batch controls.
- Capture ROM Screens processes all checked ROMs unless exactly one ROM is highlighted.
- Exactly one highlighted ROM is always captured by itself, even when its Capture box is clear.

Version 4.33 changes

- Corrected doubled ampersands in the Help menu topic names.
- Redesigned the Documentation Center with a cleaner topic list, improved typography, color accents, spacing, headings, bullets, numbered steps, and emphasized definition labels.
- Preserved the complete documentation content and Copy Topic behavior.

Version 4.32 changes

- Corrected the generic Tk title-bar icon fallback seen in v4.31.
- Added explicit PyInstaller asset-path resolution for source and one-file builds.
- Added Windows-native WM_SETICON handling for the main window, taskbar, and child windows.
- Added a dedicated 32-pixel runtime title-bar image while retaining the full PNG and multi-resolution executable ICO.

Version 4.31 changes

- Replaced the universal application icon with Wii_U_VC_Image_Builder_Console_TV.png.
- Regenerated the multi-resolution Windows ICO used by Explorer, the executable, taskbar, title bar, and desktop shortcut.

Version 4.30 changes

- Added a visible Configuration folder row with Open and Change controls.
- Added Settings menu commands to open, change, or restore the default configuration folder.
- Changing the folder copies settings.json, metadata caches, and downloaded databases to the selected location.
- The application remembers the selected configuration location across future builds.

Version 4.29 changes

- Replaced the universal application artwork with the supplied transparent PNG.
- Regenerated the multi-resolution Windows icon used by the executable, taskbar, title bar, and desktop shortcut.

Version 4.28 changes

- Universal application icon: the user-edited SVG is now the master icon source. Matching PNG and multi-resolution Windows ICO assets are used for the application window, child windows, taskbar, executable, shortcuts, and packaged releases.

- Added hover help throughout the application. Most buttons, fields, checkboxes, dropdowns, sliders, preview controls, and dialog options now show a concise explanation after the pointer rests over them.
- Dropdown tooltips describe the available choices where useful, including screen crop, Wii TV/Gamepad source, icon frames, and zoom-out fill modes.
- Added hover guidance to the image framing, template placement, game details, review, documentation, and About windows.

Version 4.26 changes

- Updated the application icon asset. The executable icon, taskbar icon, and window corner icon now use the new simplified VC-style icon.

Version 4.25 changes
--------------------
- Replaced the application/executable icon with a simpler Virtual Console artwork-oriented design.
- The new icon visually represents an icon image, TV/banner image, and GamePad output without using recognizable game artwork.
- The same icon is used by packaged Windows executables, desktop shortcuts, and the running application window where supported.
