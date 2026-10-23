// Open77 Warden for Windows: starts the Warden app's local server (embedded Python) and shows it in
// a WebView2 window.  Also installs itself (--install) and uninstalls (--uninstall).
//
//   open77-warden.exe              the window (asks once to install itself when run from the download)
//   open77-warden.exe --browser    the default browser instead of the window (keep the window open)
//   open77-warden.exe --portable   run from where it is, without the install question
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Windows.Forms;
using Fivem2Open77.Studio;

namespace Open77.Warden
{
    static class Program
    {
        public const string AppName = "Open77 Warden";

        /// <summary>The version without the build's source revision (1.0.0, not 1.0.0+abc123).</summary>
        public static string Version => Application.ProductVersion.Split('+')[0];

        /// <summary>The app's own data: settings, the servers it adds, the 3D city, the logs.</summary>
        public static string DataDir
        {
            get
            {
                string ws = Environment.GetEnvironmentVariable("O77W_WORKSPACE");
                return string.IsNullOrEmpty(ws)
                    ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "open77-warden")
                    : ws;
            }
        }

        [STAThread]
        static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            Application.ThreadException += (s, e) => Fatal(e.Exception);
            AppDomain.CurrentDomain.UnhandledException += (s, e) => Fatal(e.ExceptionObject as Exception);

            string home = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            bool has(string a) => args.Any(x => string.Equals(x, a, StringComparison.OrdinalIgnoreCase));

            if (has("--install")) return Installer.Install(home, launch: !has("--no-launch"), quiet: has("--quiet"));
            if (has("--uninstall")) return Installer.Uninstall(home, quiet: has("--quiet"));

            // run from the download (not installed yet): offer to install first
            if (!Installer.IsInstalled(home) && !has("--portable") && Installer.OfferInstall(home))
                return 0;

            using (var mutex = new Mutex(true, "open77-warden-" + Environment.UserName, out bool first))
            {
                if (!first)
                {
                    WindowFinder.ActivateExisting();
                    return 0;
                }
                if (Installer.IsInstalled(home)) new Thread(() => Installer.Sweep(home)) { IsBackground = true, Priority = ThreadPriority.BelowNormal }.Start();
                using (var backend = new Backend(home))
                {
                    Application.Run(new MainForm(backend, has("--browser")));
                }
            }
            return 0;
        }

        static void Fatal(Exception e)
        {
            try
            {
                Directory.CreateDirectory(DataDir);
                File.AppendAllText(Path.Combine(DataDir, "host-errors.log"), DateTime.Now.ToString("s") + " " + e + Environment.NewLine);
            }
            catch { }
            MessageBox.Show("Something went wrong:\n\n" + (e?.Message ?? "unknown error"), AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    static class WindowFinder
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
        [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);

        /// <summary>The window of the copy already running, to the front.</summary>
        public static void ActivateExisting()
        {
            foreach (var p in Process.GetProcessesByName(Process.GetCurrentProcess().ProcessName))
            {
                if (p.Id == Process.GetCurrentProcess().Id || p.MainWindowHandle == IntPtr.Zero) continue;
                ShowWindow(p.MainWindowHandle, 9);   // SW_RESTORE
                SetForegroundWindow(p.MainWindowHandle);
                return;
            }
        }
    }
}
