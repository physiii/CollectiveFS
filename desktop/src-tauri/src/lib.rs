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

pub fn run() {
    let mut builder = tauri::Builder::default();

    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            focus_main_window(app);
        }));
    }

    builder
        .run(tauri::generate_context!())
        .expect("error while running the CollectiveFS desktop shell");
}
