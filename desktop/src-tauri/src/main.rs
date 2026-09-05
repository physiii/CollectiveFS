#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// WebKitGTK's accelerated compositing path can leave a painted-but-inert window
// on some proprietary GPU stacks (notably NVIDIA). The fix affects process
// creation, so it must be in the environment before WebKit starts — we re-exec
// ourselves once with it set. Ported from the sibling `spectra`/`terminal` apps.

#[cfg(target_os = "linux")]
fn nvidia_driver_present() -> bool {
    std::path::Path::new("/proc/driver/nvidia/version").is_file()
}

fn main() {
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::process::CommandExt;
        if std::env::var_os("CFS_RENDER_FIX").is_none() {
            let disable = std::env::var_os("CFS_DISABLE_COMPOSITING").is_some();
            let enable = std::env::var_os("CFS_ENABLE_COMPOSITING").is_some();
            let safe = disable || (nvidia_driver_present() && !enable);
            let mut cmd = std::process::Command::new(std::env::current_exe().unwrap());
            cmd.args(std::env::args_os().skip(1));
            cmd.env("CFS_RENDER_FIX", "1");
            if safe {
                cmd.env("WEBKIT_DISABLE_COMPOSITING_MODE", "1");
                cmd.env("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
                cmd.env("LIBGL_ALWAYS_SOFTWARE", "1");
            }
            let err = cmd.exec();
            eprintln!("re-exec for render fix failed: {err}");
        }
    }
    collectivefs_desktop_lib::run();
}
