from __future__ import annotations

APP_VERSION = "5.06.0"

HELP_SECTIONS: list[tuple[str, str]] = [
    (
        "Updates and Settings",
        """STUDIO UPDATES

Updates in the top bar checks the public stable release feed. Startup checks run
without waiting for metadata work. The app also checks every five minutes while
online and on focus return after at least two minutes. Errors and offline checks
are not reported as up to date. Manual checks and retries have cooldowns.

Download is explicit. Install and restart is separate and blocked during active
operations. The current ROM list closes on restart; exported files and settings
remain. Downloads are checked against the repository manifest, archive safety
rules and Windows executable identity. Hashes are not publisher signatures.

Settings retain the existing Wii_U_VC_Image_Builder configuration directory and
any custom configuration pointer. Update staging and one rollback executable per
installation live beneath LocalAppData/Wii_U_VC_Image_Studio/updates. Failed
health checks trigger rollback. Failed staging is retained for diagnosis.

Image Builder v5.05 has no updater and requires a manual first Studio upgrade.
Extract Studio separately and repoint old shortcuts explicitly. Later in-app
updates retain the current executable name, including custom filenames.
""",
    ),
    (
        "Getting Started",
        """Wii U VC Image Studio creates artwork and reference metadata for Wii U Virtual Console projects.

Version 5 uses a local WebUI workspace inside a native Windows window. Game files, screenshots, downloaded assets, configuration, and generated outputs remain on your computer. The interface does not upload ROMs to a hosted service.

QUICK START

1. Set an Export folder.
2. Add one or more ROMs with Add ROMs, Add ROM Folder, or by dropping ROM files or folders onto the application.
3. The application identifies the source system, begins metadata lookup, and searches enabled artwork and boot-sound sources automatically. Exact product codes are preferred, with conservative title and region fallbacks available when exact art is absent.
4. Select a game.
5. For batch capture, check the ROMs you want in the Capture column. If exactly one ROM is highlighted, that ROM runs alone even if its Capture box is not checked.
6. Either capture a title screen through an emulator or use Import Screenshot for Selected Game.
7. Review the generated Title Screen, Icon Image, TV Image, and Gamepad Image in the preview workspace. Click any preview image to open it near the available screen size in a separate resizable window.
8. Use Edit Game Details, the icon-frame selector, Adjust Image Crop & Zoom, and the per-game Boot Sound controls as needed.
9. Open Output when the artwork is ready.

HOVER HELP

Most controls include brief hover help. Rest the pointer over a button, field, checkbox, dropdown, slider, preview, or dialog option for about half a second to see what it does and, where useful, what its selections mean.

Hover help closes automatically after a readable interval. It also closes when you move the pointer a meaningful distance, click, scroll, type, change focus, or leave the control. After dismissal, it stays hidden until the pointer leaves and re-enters the control.

The application never requires emulator capture. If you already have a suitable screenshot, importing it is usually the fastest workflow.

ROMs, game keys, RetroArch, Dolphin, and emulator cores are not included. Use your own legally obtained game dumps and emulator installations.
""",
    ),
    (
        "Main Workflow",
        """ADDING GAMES

Add ROMs loads individual files. Add ROM Folder scans a folder for supported game-file extensions. You can also drag supported ROM files or folders from Windows Explorer and drop them anywhere on the main application window or ROM list. Dropped folders are scanned recursively. All three methods use the same duplicate protection, ROM validation, metadata creation, and automatic metadata lookup. Ctrl+O opens Add ROMs, and Ctrl+Shift+O opens Add ROM Folder. A ROM must be loaded before a manually imported screenshot can be attached to it.

The top toolbar contains the primary add, import, metadata, artwork-search, and build actions. Game-specific editing, output, capture, and sound controls appear in the tabbed panel beneath the ROM list.

METADATA

When a ROM is added, header or filename information is available immediately and metadata.txt is created. The application then performs its public metadata lookup and refreshes the record when a better match is found.

CAPTURE OR IMPORT

The Capture column controls batch capture. Check Selected and Uncheck Selected form one state-aware toggle for highlighted ROMs. If every highlighted ROM is checked, the button unchecks them; otherwise it checks them. Check All enables every ROM, and Clear Checks clears the batch. Exactly one highlighted ROM always takes priority and is captured by itself, even when its box is clear.

Each row also includes an X in the Remove column. Click it to remove only that row from the current application list. This does not delete the source ROM or generated output files.

The checkbox controls beneath the list provide the selected-row toggle, Check All, Clear Checks, and Remove Checked. Remove Checked operates only on ROMs whose Capture boxes are checked; merely highlighting a row does not include it in removal. Delete is a shortcut for Remove Checked when the ROM list has focus.

For emulator capture, configure RetroArch.exe. GameCube, Wii, and WiiWare can use the RetroArch Dolphin core when available, with a configured Dolphin.exe as a fallback.

For manual artwork, select a loaded game and choose Import Screenshot for Selected Game. Wii and WiiWare can also use an independent Custom TV/Gamepad Image while leaving the Icon Image based on the selected screenshot.

REVIEW

Review Selected shows retained candidate frames. Use This Frame and Re-export changes the selected title screen without a blocking confirmation prompt. Done closes the review window.

When an artwork search retains alternatives, Artwork Match Review lists every repository and artwork type that decoded successfully, including possible matches that require your decision. The visible Review Artwork Matches button in the Game tab shows how many are available. Select a match to preview it and switch among its available source/Icon/TV/Gamepad assets. Use for All Outputs keeps the one-click workflow. Use for Icon Image and Use for TV/Gamepad Images let those output groups use different retained matches.

OUTPUT

The generated files are stored beneath the Export folder in a system folder and a game-specific folder. Open Output opens the selected game's output directory.
""",
    ),
    (
        "Artwork & Framing",
        """GENERATED ARTWORK

Title Screen: the selected or imported source screenshot.
Icon Image: 128 x 128.
TV Image: 1280 x 720.
Gamepad Image: 854 x 480.

ICON FRAMES

For many legacy systems, Light Frame and Dark Frame choices are available. WiiWare Frame and Wii Frame are separate choices for Wii-family titles. Screenshot only removes the decorative icon frame.

ADJUST IMAGE CROP & ZOOM

Use Adjust Crop & Zoom in the Game tab. Its compact left-side dialog uses no faded or blurred backdrop, leaving the Selected Output area clear for live review. A separate preview window can also remain open beside the main workspace while you adjust the controls.

Each output has independent controls for:
- Horizontal focus
- Vertical focus
- Zoom
- Fill when zoomed out

Focus values run from 0 to 100. A value of 50 centers the image. Zoom 100% is the normal fit. Higher values zoom in. Lower values zoom out.

The TV and Gamepad position/zoom controls can be linked so changes to either are mirrored to the other.

ZOOM-OUT FILL

Black: fills exposed space with black.
Blurred Image: fills exposed space with a blurred full-frame version of the same source artwork.
Solid Color: fills exposed space with the selected custom color.

Each position and zoom control has its own Reset button, and Reset All restores the entire framing dialog to defaults.

Changes update the preview automatically. Rapid slider movement is coalesced so obsolete renders do not build up behind the most recent setting. The status beneath the controls reports pending and completed preview updates. Done keeps the current settings. Cancel restores the settings that were active when the dialog opened.

SOURCE IMAGE AREA

Adjust Image Crop & Zoom provides Automatic, Full Image, Top, Bottom, Left, and Right choices for every system. Use Top or Bottom for vertically stacked dual-screen artwork and Left or Right for side-by-side or vertically oriented source layouts. The source area is chosen first, then the existing Icon, TV, and Gamepad focus, zoom, and fill controls are applied.

Downloaded Libretro source artwork rebuilds immediately while these controls are changed. A metadata refetch is not required.

TEMPLATE PLACEMENT / SIZE

This is different from image framing. Template Placement / Size changes the screenshot destination rectangle in the TV template. Adjust Image Framing changes which part of the source image is visible within that rectangle.

ARTWORK FONT

Artwork font selection is kept out of the main workspace. Open Settings and use Browse beside Artwork font to select an OTF or TTF file. Clear the path to return to the automatic/default font. The selection is saved in settings.json.
""",
    ),
    (
        "Game Details & Banner Text",
        """EDIT GAME DETAILS

Edit Game Details contains:
- Game name
- Released year
- Players
- Product code

GAME NAME LINES

The game name can contain up to three explicit lines. Press Enter in the Game name field where you want a line break. Those manual line breaks are preserved when banner text is generated, with font sizing adjusted to fit the available frame area.

BANNER TEXT OPTIONS

The TV and Gamepad artwork can optionally include:
- Game title
- Release date
- Player count

The master metadata-overlay option controls whether those text objects are drawn.

For Wii and WiiWare, title text is confined to the frame area above the screenshot and release/player information is confined below it. The screenshot opening itself is not reduced to make room for metadata text.

Editing game details also refreshes the generated metadata and artwork for a game that already has a selected screenshot.
""",
    ),
    (
        "Metadata",
        """AUTOMATIC LOOKUP

Metadata lookup begins when a ROM is added. The application uses public, no-account metadata sources, primarily Libretro Database data with GameTDB supplementation where appropriate.

Matching can use ROM hashes, normalized cartridge hashes, product/disc IDs, and conservative title matching depending on the system and available source data.

The application includes special handling for common dump-format differences such as NES headers, SNES copier headers, and Nintendo 64 byte order.

MANUAL CORRECTION

Public databases are not complete. Hacks, prototypes, homebrew, unusual dumps, regional variants, or compressed disc images may have missing or ambiguous data. Use Edit Game Details to correct the game name, year, players, or product code.

METADATA.TXT

metadata.txt is created even before a title screenshot is available. It is intended as a convenient reference for the game and the generated files. Metadata text generation is independent of whether banner text overlays are enabled.

UPDATE DATABASE

Update Database refreshes locally cached metadata sources used by the application. Fetch Metadata retries lookup for the selected game.
""",
    ),
    (
        "Emulator Capture Setup",
        """RETROARCH

Set RetroArch.exe in the path strip at the top of the workspace. The application looks for compatible installed Libretro cores for the selected system.

For Game Boy, Game Boy Color, and Game Boy Advance, the application uses GBA-capable cores such as mGBA or VBA-M rather than requiring separate GB/GBC-only cores.

GAMECUBE / WII / WIIWARE

The preferred capture path is the RetroArch Dolphin Libretro core. That core also requires the Dolphin Sys files beneath RetroArch's configured system directory.

If the Dolphin Libretro core is unavailable, incomplete, fails during capture, or produces unusable black frames, the application can fall back to the configured external Dolphin.exe.

DOLPHIN.EXE

Dolphin.exe is optional unless the fallback is needed. It is not included with this application.

CAPTURE SETTINGS

The compact guide at the top of the panel summarizes the available groups without taking substantial workspace. Detailed explanations remain available by hovering over each control.

Warm-up seconds: delay before sampling begins.
Scan duration: how long the application samples the running game.
Sample interval: delay between captured samples.
Candidates to keep: number of high-scoring frames retained for Review Selected.
Screen crop: controls source-screen cropping. Nintendo DS capture also evaluates individual screens automatically.

STOP AND USE CAPTURES

This stops the current emulator session, analyzes the frames already collected, exports the best available result, retains review candidates, and stops the batch rather than discarding the capture work.
""",
    ),
    (
        "Supported Systems & Files",
        """SUPPORTED SOURCE SYSTEMS

- Nintendo DS
- Game Boy Advance
- Game Boy Color
- Game Boy
- NES
- SNES
- Nintendo 64
- GameCube
- Wii
- WiiWare
- MSX
- TurboGrafx-CD / PC Engine CD
- Genesis / Mega Drive
- Master System
- Game Gear
- TurboGrafx-16 / PC Engine

SUPPORTED INPUT EXTENSIONS

.nds, .gba, .gb, .gbc, .nes, .sfc, .smc, .n64, .z64, .v64, .gcm, .iso, .rvz, .ciso, .wbfs, .wad, .rom, .mx1, .mx2, .cue, .md, .gen, .smd, .sms, .gg, .pce

Some extensions can represent more than one console. The application inspects supported headers where possible to distinguish the actual system. Compressed or unusual disc formats may provide less header metadata and can require manual Product Code correction.

Wii U-native game files are intentionally not supported. This utility creates assets for Wii U Virtual Console projects; it does not treat native Wii U titles as Virtual Console source games.
""",
    ),
    (
        "Repository Artwork",
        """MULTI-SOURCE SEARCH

When Automatically Search Artwork and Boot-Sound Sources is enabled under Settings, Repository Artwork, the application checks community artwork and available boot sounds after metadata lookup finishes. Automatic search is enabled by default and can be disabled without affecting previously downloaded files.

Exact product-code matches remain the highest-priority result. When Allow Title and Metadata Fallbacks is enabled, the application can continue with metadata title, internal ROM title, filename title, and region evidence. Confident matches can be applied automatically. When Include Possible Matches for Manual Review is enabled, lower-confidence but decodable images are retained without being applied so the user can make the final decision.

SOURCE ORDER

1. UWUVCI Community Images, a maintained product-code collection derived from and expanded beyond the original Cucholix artwork.
2. Cucholix WiiVC BIS, used as the original-source fallback when the maintained collection has no match.
3. GameCube Inject Artwork, a title-matched collection of finished GameCube Wii U icon and TV artwork.
4. Libretro Thumbnails, searched for every supported console. The preferred source type is title-screen artwork, followed by gameplay screenshots, box art, and logos.

The application does not bundle these collections. A repository image is file-usable when it uses a supported image extension, is no larger than 24 MB, and can be decoded as image pixels. This technical validation is separate from title-match confidence. The application records the selected repository, match method, matched title, artwork type, confidence, and available product code in metadata.txt.

If a raw repository download cannot be decoded, the application retries through GitHub's media endpoint. A bad artwork type is skipped without discarding other valid title screens, gameplay screenshots, box art, or logos found for the same game. A response that still cannot be decoded cannot be shown in review because it contains no renderable image pixels.

MANUAL LOOKUP

Select one ROM and choose Search Artwork in the main toolbar. A product code is not required when title fallbacks are enabled, although entering an accurate title, product code, and region under Edit Game Details improves matching.

MULTIPLE MATCHES

When more than one usable match exists across repositories or artwork types, the application retains them together. The visible Review Artwork Matches button in the Game tab shows the retained count and opens Artwork Match Review. Manual search retains the same choices. Automatic search applies only a confident highest-priority result; possible low-confidence matches wait for manual review.

Each retained match can be assigned independently. Use for Icon Image changes only Icon Image.png. Use for TV/Gamepad Images changes only TV Image.png and Gamepad Image.png. Use for All Outputs applies the selected match everywhere it provides usable artwork. The current assignment for each output group is shown in the match list and details panel.

USING THE RESULT

Downloaded exact iconTex artwork becomes Icon Image.png. Downloaded bootTvTex artwork becomes TV Image.png and is resized for Gamepad Image.png when a separate bootDrcTex file is unavailable. Finished GameCube artwork is applied in the same way.

Libretro source images pass through the normal renderer so generated outputs use the application's selected template, banner text, and framing controls. When separate matches are assigned, the Icon source is rendered only into Icon Image.png and the TV/Gamepad source is rendered only into the two banner outputs. Framing, generic source-area choice, icon-frame, template-placement, and metadata-overlay changes rebuild each assigned source immediately; metadata does not need to be refetched.

Repository artwork remains active for later rebuilds and is identified in the ROM list Source column. Choose Use Generated Artwork in the Game tab to return that game to the application's normal screenshot-and-template artwork.

Capturing a new screen or explicitly importing a screenshot makes that user-selected image the active source and replaces downloaded artwork in the outputs. The downloaded file stays in the cache, but automatic artwork lookup will not silently reapply it. A later manual artwork search can select repository artwork again.

CACHE AND TERMS

Downloads are cached beneath repository_artwork_cache in the active configuration folder. Open Settings, Repository Artwork, Open Download Cache to inspect them.

Community artwork can have source-specific rights or licensing terms. Review the selected repository and the rights applicable to a particular image before redistribution. The sharing kit contains downloader code and attribution only, not downloaded community artwork.
""",
    ),
    (
        "Boot Sounds",
        """OFFLINE BUILT-IN CHOICES

The Boot Sound selector includes two original packaged choices that work without an internet connection: Generic Retro Startup and Generic Modern Startup. Third-party console recordings are not bundled with the application.

Select one game, open the Boot Sound list, and choose any built-in option. Play Boot Sound previews that exact file, and the normal export path converts it to bootSound.wav. A built-in choice remains active if a later online lookup finds other candidates, unless you explicitly select a different sound.

AUTOMATIC SOUND SEARCH

When automatic source search is enabled, the application checks Cucholix WiiVC BIS for a boot sound associated with the exact product code. If no exact game sound is available, it can use a generic sound supplied for that console. GameCube also checks the generic WAV choices in GameCube Inject Artwork.

Exact per-game sounds have priority over console defaults. When several choices are available, the highest-priority result becomes active and every retained option appears in the selected game's Boot Sound selector.

MANUAL AND WEB SELECTION

Select one game and use Browse in the Boot Sound panel to choose a local WAV, MP3, or BTSND file for that game. The application converts it to WAV and stores a private working copy beneath game_boot_sounds in the active configuration directory. That per-game sound is restored automatically when the same ROM is added during a later session, even if the original downloaded file has been moved. Search Sources performs the configured repository lookup. More Sounds opens a console-specific MyInstants search in the default browser. Download only a sound you are permitted to use, then import it with Browse for one game or Set Console for the whole system. The application does not scrape or bundle MyInstants-hosted audio.

Play Boot Sound stops any earlier preview, refreshes the conversion from the active selection, and plays a unique temporary copy so a previously playing console default cannot be mistaken for the selected manual file. The status line and activity log identify the exact source being played. Stop ends playback. Clear removes the active choice and generated bootSound file. When the active choice is a stored per-game import, Clear also removes its private configuration copy while leaving the original downloaded or local file untouched.

CONSOLE-SPECIFIC STARTUP SOUNDS

Downloaded generic choices are labeled with their console, such as Game Boy Advance console startup, and remain separate from per-game sounds. Set Console imports a WAV, MP3, or BTSND file as the reusable startup sound for the selected game's console. It is converted once and stored beneath console_boot_sounds in the active configuration folder. The local console sound becomes the default for current and future games from that system without replacing an existing per-game or manual override on other loaded games. This works for every supported console, including systems that do not have an online sound source.

OUTPUT

The active built-in, stored per-game, downloaded repository, or local console WAV/MP3/BTSND source is automatically decoded and written to the game output folder as bootSound.wav. BTSND is decoded as its native stereo, 16-bit, 48 kHz PCM audio. MP3 and encoded WAV files use the bundled miniaudio decoder. Existing bootSound files with another supported extension are removed so only bootSound.wav remains. metadata.txt records the selected source and converted output filename.

UWUVCI's documented launch playback uses the first six seconds, so trim or prepare longer audio appropriately before selection. Conversion preserves the available audio rather than trimming it, and the original downloaded or manually selected file is never modified.

CACHE AND RIGHTS

Downloaded repository sounds are cached beneath repository_artwork_cache, boot_sounds in the active configuration folder. Per-game imports are stored beneath game_boot_sounds, and console-wide imports are stored beneath console_boot_sounds. Only the two original generic chimes are bundled with the shared application. Audio can have separate copyright and redistribution terms from artwork. Confirm that you have permission to use and redistribute downloaded or manually selected sounds.
""",
    ),
    (
        "Output Files",
        """A completed game can produce the following files:

TITLE_SCREEN.PNG
The selected captured frame or manually imported source screenshot.

ICON IMAGE.PNG
128 x 128 icon artwork using the chosen system frame or Screenshot only mode.

TV IMAGE.PNG
1280 x 720 TV artwork.

GAMEPAD IMAGE.PNG
854 x 480 GamePad artwork.

METADATA.TXT
Reference information including detected or edited game details and generated-file information.

BOOTSOUND.WAV
Optional manual, per-game, or console-specific launch audio. WAV, MP3, and BTSND selections are converted automatically, and only the WAV output is kept active.

OUTPUT SELECTION

Icon Image, TV Image, and Gamepad Image can be enabled or disabled independently with the three output checkboxes in the Capture tab. If an optional output is disabled during a rebuild, the application removes the older stale version rather than leaving a misleading previous file in the output folder.

An exact finished-art match can replace the generated Icon, TV, and Gamepad outputs while leaving title_screen.png unchanged. A Libretro source image assigned to all outputs is rendered through the normal output pipeline and becomes the active title_screen.png source. Assigning repository matches separately changes only Icon Image or the paired TV/Gamepad images while preserving the other assignment. metadata.txt records each output assignment, source, and matching evidence used.
""",
    ),
    (
        "Activity Log & File Logging",
        """ACTIVITY PANEL

The Activity panel reports metadata matches, capture progress, generated files, source changes, and errors. Drag its lower edge to resize it without changing the size of the other controls.

Hide collapses the panel without stopping activity collection or file logging. Choose Show in the same panel to restore it.

DETACH AND REATTACH

Detach moves Activity into an independently resizable window and hides the embedded panel. Choose Reattach or close the detached window to return Activity to the main workspace.

LIVE FILE LOGGING

Open Settings, Activity Logging to open the dedicated logging settings window. It provides:
- An Enable file logging checkbox.
- An editable log output directory.
- Browse and Open Folder controls.
- Save and Cancel buttons so changes are not applied accidentally.

Selecting a log output folder enables live file logging by default. Each application session receives a timestamped UTF-8 .log file, and new Activity messages are appended immediately. Disabling file logging does not clear the configured folder and does not delete existing logs.

The Activity panel may be resized, detached, or hidden while file logging continues.

WINDOW AND PANEL RESIZING

The main window receives its initial size only once during startup. After that, selecting ROMs, refreshing previews, or rebuilding artwork does not replace the size and position chosen by the user.

The application shell always fits the usable WebView viewport and does not use a page-level horizontal or vertical scrollbar. The header, ROM list, main workspace, operation status, and Activity panel resize relative to the available window height. At smaller heights, spacing and pane heights compact automatically without shrinking the text below a practical reading size.

The ROM list and Activity panel each have a standard resize handle at the lower edge. A visible vertical resize bar separates Selected Game controls from Selected Output previews. Selected Game uses roughly two-thirds of the workspace by default, while Selected Output remains visible at the right and can be resized with the divider. Preview images use the original two-by-two order: Title Screen, Icon Image, TV Image, and Gamepad Image. They scale to the available preview tiles. Detach Preview moves the complete synchronized preview grid into a separate window.

Overflow remains local to the relevant region. The ROM table scrolls horizontally and vertically when every row or column cannot fit, the selected-game tab scrolls independently when its controls exceed the pane, and Activity messages scroll inside the Activity panel. At narrow widths, the stacked Selected Game and Selected Output workspace has its own internal scrollbar. These local scroll areas keep the full application frame stationary.

Required controls have practical minimum pane sizes so the vertical resize bar cannot collapse them out of reach. The ROM command bar stays outside the scrolling table.

The toolbar uses multiple flexible rows so its controls remain available at practical window widths. Explanatory text wraps to the width available in its panel.

CONFIGURATION FOLDER

Configuration-folder controls are available from Settings. They control where settings.json, metadata caches, downloaded databases, repository artwork, and downloaded boot sounds are stored. The activity log output folder is configured separately.
""",
    ),
    (
        "Troubleshooting",
        """NO METADATA MATCH

Use Fetch Metadata to retry. Verify Product Code in Edit Game Details. Some titles are simply absent or ambiguous in public databases, in which case enter the missing values manually.

NO REPOSITORY ARTWORK MATCH

Verify the title, Product Code, and Region under Edit Game Details. Under Settings, Repository Artwork, confirm that title and metadata fallbacks, Include Possible Matches for Manual Review, and the desired sources are enabled. Possible matches appear behind the visible Review Artwork Matches button and are never applied automatically. If no decodable image exists, capture a title screen or import an image normally.

REPOSITORY IMAGE IS NOT USABLE

The repository response was under the size limit but could not be decoded as image pixels. The application retries through GitHub's media endpoint and continues with other valid artwork types. An item that fails both downloads cannot be previewed; use another retained match or import an image manually.

BLACK OR EMPTY GAMECUBE/WII CAPTURE

Verify the RetroArch Dolphin core and its required Sys directory. If configured, the application can fall back to external Dolphin.exe. A manually imported screenshot can always be used instead of emulator capture.

WRONG PART OF THE SCREENSHOT IS VISIBLE

Open Adjust Image Framing and change Horizontal focus, Vertical focus, or Zoom. Use the detached preview if you want a larger view while adjusting.

SOURCE IMAGE SHOWS THE WRONG HALF

Open Adjust Image Crop & Zoom and choose Top, Bottom, Left, or Right. The generic source-area choice and the existing framing controls update downloaded or imported source artwork immediately.

DOWNLOADED SOURCE ART DOES NOT REFLECT A SETTING

Select the game and change the setting again. Framing, source-area choice, icon frame, template placement, and metadata overlay should rebuild Libretro source artwork immediately. Finished Wii U icon and boot-image collections are complete output assets rather than source screenshots, so source-image framing is not applied to those finished files.

NO BOOT SOUND WAS FOUND

Repository sound coverage is not complete for every title or console. Confirm the Product Code under Edit Game Details, then use Search Sources in the Boot Sound panel. If no online result exists, use Browse to select a local WAV, MP3, or BTSND file.

BOOT SOUND IS NOT IN THE OUTPUT

Select the game and confirm that the Boot Sound selector shows the expected active file and source. Re-select the option or browse for the file again if the original manual path was moved. Play stops any prior preview, reconverts the active source, and uses a unique temporary filename so an older sound cannot continue playing. Use Set Console when you want one reusable startup sound for every game from that system.

BLACK BARS WHEN ZOOMING OUT

Change Fill when zoomed out to Blurred Image or Solid Color.

TITLE DOES NOT BREAK WHERE EXPECTED

Open Edit Game Details and insert explicit line breaks in Game name. Up to three lines are supported.

WII TV/GAMEPAD ARTWORK SHOULD USE A DIFFERENT IMAGE

Use Import Custom TV/Gamepad Image. The icon can remain based on the selected title screenshot. Use Use Selected Screenshot for TV/Gamepad to switch back.

PREVIEW WINDOW

Click any image in the preview workspace to open the reusable enlarged preview window near the usable screen size. The image fills as much of that window as its aspect ratio permits, including low-resolution Icon and title-screen sources, and refits whenever the window is resized. Clicking another preview updates the same window. Detach Preview instead moves the complete four-image preview grid into a separate synchronized window with Reattach Preview and Adjust Crop and Zoom controls.

ACTIVITY LOG IS HIDDEN

Choose Show in the Activity panel if it was manually hidden. If Activity was detached, choose Reattach in the detached window or close that window. Detaching and hiding do not stop activity collection or enabled live file logging.

LOG FILE IS NOT BEING CREATED

Open Settings, Activity Logging. Confirm that Enable file logging is checked, a writable log output directory is selected, and Save has been clicked. The Activity status line shows the current state and folder.

CAPTURE SETTINGS OR ROM CONTROLS APPEAR CROWDED

Use the visible resize bars to rebalance the panels. Capture Settings can be scrolled with its scrollbar or the mouse wheel. ROM rows and Activity messages also scroll inside their own panels. The application frame itself remains fitted to the window and does not require a page-level scrollbar. The application also enforces minimum window and pane sizes so required controls cannot be resized completely out of reach.
""",
    ),
    (
        "Credits & Notices",
        """Wii U VC Image Studio is an independent community utility and is not affiliated with or endorsed by Nintendo.

The application uses or interoperates with community projects and public metadata sources including Libretro/RetroArch databases and GameTDB where applicable.

Optional artwork and repository sound search uses UWUVCI Community Images, the original Cucholix WiiVC BIS collection, GameCube Inject Artwork, and the per-console Libretro Thumbnails repositories. More Sounds opens a console-specific search on MyInstants but does not scrape or bundle MyInstants-hosted audio. Downloaded repository content is cached on demand. Users must review each source's terms and the rights applicable to each selected file before redistribution.

This release contains only two original generic startup chimes created for the application. No MyInstants-derived console recordings are bundled in the source, Sharing Kit, or generated executable.

The WebUI desktop shell and native file-dialog and drag-and-drop bridge are provided by pywebview, distributed under the BSD 3-Clause license.

MP3 and encoded-WAV decoding is provided by Python miniaudio and the miniaudio C library, distributed under the MIT license. Wii U BTSND decoding is implemented directly from its documented stereo PCM layout.

Some Virtual Console template resources included with the project originated from or were modeled after resources from the open-source UWUVCI project. Retain the applicable attribution and license notices when redistributing builds that include those resources.

RetroArch and Dolphin are separate projects and are not bundled with this application.

Nintendo and the names of Nintendo systems are trademarks of Nintendo.

Users are responsible for using legally obtained game dumps and for complying with applicable laws and software licenses.
""",
    ),
]

HELP_BY_TITLE = dict(HELP_SECTIONS)
