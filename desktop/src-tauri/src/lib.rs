//! CollectiveFS desktop shell.
//!
//! Wraps the CollectiveFS node console (a static Vite/React app served by the
//! node at `http://localhost:8010`) in a native webview: WKWebView on macOS,
//! WebKitGTK on Ubuntu. The bundled splash (`dist/index.html`) paints instantly
//! and then navigates to the node, so the file browser and live metrics run as
//! their own application. The target URL is an in-app setting (localStorage
//! `cfs.server.url`), defaulting to the local node.

#[cfg(desktop)]
fn focus_main_window(app: &tauri::AppHandle) {
    use tauri::Manager;
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Read a file the user dropped onto the window (native drag-drop gives paths,
/// not bytes). Returned as a raw IPC Response so large files (e.g. video) travel
/// efficiently to the webview instead of as a JSON number array. The frontend
/// wraps the bytes in a File and uploads them to the node like any other upload.
#[tauri::command]
fn read_dropped(path: String) -> Result<tauri::ipc::Response, String> {
    std::fs::read(&path)
        .map(tauri::ipc::Response::new)
        .map_err(|e| format!("{path}: {e}"))
}

/// Bring up the CollectiveFS FUSE mount so every file in the collective is a
/// real path the OS can open — double-clicking a video in Files launches the
/// native player, which streams it from the mesh instead of downloading it.
///
/// Best-effort and non-blocking: the mount is a convenience, never a launch
/// gate. On Linux we hand off to the per-user `cfs-mount.service` (it self-heals
/// and picks its node by mDNS/config), starting it only if it is not already
/// active so we never disturb a mount the user brought up themselves. macOS
/// (macFUSE) and Windows (WinFsp) need their own mount backends — tracked
/// separately — so there we no-op rather than pretend.
#[cfg(target_os = "linux")]
fn ensure_mount() {
    use std::process::Command;
    let active = Command::new("systemctl")
        .args(["--user", "is-active", "--quiet", "cfs-mount.service"])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if active {
        return;
    }
    match Command::new("systemctl")
        .args(["--user", "start", "cfs-mount.service"])
        .status()
    {
        Ok(s) if s.success() => eprintln!("cfs: mount service started"),
        Ok(s) => eprintln!("cfs: mount service not started (exit {s}); is it installed?"),
        Err(e) => eprintln!("cfs: could not invoke systemctl ({e}); skipping mount"),
    }
}

#[cfg(not(target_os = "linux"))]
fn ensure_mount() {}

pub fn run() {
    let mut builder = tauri::Builder::default();

    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            focus_main_window(app);
        }));
    }

    builder
        .setup(|_app| {
            // Off the UI thread: mounting waits on node discovery + FUSE, and
            // the window must paint regardless of whether a mount comes up.
            std::thread::spawn(ensure_mount);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![read_dropped])
        .run(tauri::generate_context!())
        .expect("error while running the CollectiveFS desktop shell");
}
