// The Warden window: WebView2 showing the Warden app (the servers, then a server's live map beside its
// own Warden panel), behind the HUD start screen until the page has loaded.  Falls back to the default
// browser when the WebView2 runtime is missing.
//
// The app's pages live on this PC's loopback under *.localhost names (one per server, so each
// server's Warden keeps its own sign-in).  The window stays on the app's pages: a link anywhere else
// opens in the default browser, and a server page that tries to take over the whole window (a panel
// breaking out of its frame) is kept in its frame.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;
using Fivem2Open77.Studio;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Open77.Warden
{
    sealed class MainForm : Form
    {
        readonly Backend backend;
        readonly bool useBrowser;
        WebView2 web;
        readonly HudSplash splash;
        bool shown;
        readonly string stateFile = Path.Combine(Program.DataDir, "window.txt");

        public MainForm(Backend backend, bool useBrowser)
        {
            this.backend = backend;
            this.useBrowser = useBrowser;
            Text = Program.AppName;
            Icon = AppIcon.Load() ?? Icon;
            ForeColor = Color.FromArgb(230, 233, 240);
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(1000, 680);
            Size = new Size(1500, 940);
            Font = new Font("Segoe UI", 10f);
            BackColor = Color.FromArgb(3, 5, 10);
            splash = new HudSplash { Dock = DockStyle.Fill, Version = Program.Version, Caption = "OPEN//77", Tagline = "WARDEN",
                                     Status = "Starting the Warden app" };
            Controls.Add(splash);
            LoadWindowState();
            Shown += async (s, e) => await StartAsync();
            FormClosing += (s, e) => SaveWindowState();
        }

        async Task StartAsync()
        {
            splash.Detail = backend.Workspace;
            try
            {
                await Task.Run(() => backend.Start(TimeSpan.FromSeconds(120)));
            }
            catch (Exception ex)
            {
                var r = MessageBox.Show(this, "Open77 Warden could not start:\n\n" + ex.Message + "\n\nOpen the log folder?",
                    Program.AppName, MessageBoxButtons.YesNo, MessageBoxIcon.Error);
                if (r == DialogResult.Yes) OpenFolder(Path.GetDirectoryName(backend.LogFile ?? backend.Workspace));
                Close();
                return;
            }
            backend.Exited += code => BeginInvoke((Action)(() =>
            {
                MessageBox.Show(this, "The Warden app's server stopped (exit " + code + ").\n\n" + backend.Tail(), Program.AppName,
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
            }));
            if (useBrowser) { BrowserMode(); return; }
            splash.Status = "Loading the interface";
            splash.Detail = backend.SafeUrl;
            try
            {
                web = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.FromArgb(3, 5, 10) };
                string data = Path.Combine(Program.DataDir, "webview2");
                var env = await CoreWebView2Environment.CreateAsync(null, data);
                Controls.Add(web);
                splash.BringToFront();
                await web.EnsureCoreWebView2Async(env);
                var core = web.CoreWebView2;
                core.NavigationCompleted += (s, e) =>
                {
                    if (shown) return;
                    if (!e.IsSuccess)
                    {
                        splash.Status = "The interface did not load";
                        splash.Detail = e.WebErrorStatus + " - " + backend.SafeUrl;
                        splash.SetAction("Try again", () => web.Source = new Uri(backend.Url));
                        return;
                    }
                    shown = true;
                    splash.Status = "Online";
                    splash.Progress = 100;
                    var hide = new Timer { Interval = 220 };
                    hide.Tick += (s2, e2) =>
                    {
                        hide.Dispose();
                        web.BringToFront();
                        splash.Visible = false;     // its timer stops with it
                        web.Focus();
                    };
                    hide.Start();
                };
                core.Settings.IsStatusBarEnabled = false;
                core.Settings.AreDefaultContextMenusEnabled = true;
                core.Settings.IsZoomControlEnabled = true;
                core.Settings.IsPasswordAutosaveEnabled = true;     // (the WebView offers to keep a Warden sign-in: the operator's choice)
                core.NewWindowRequested += (s, e) =>
                {
                    e.Handled = true;
                    if (IsAppPage(e.Uri)) web.Source = new Uri(e.Uri);     // (an app page asked for its own window: here)
                    else OpenExternal(e.Uri);
                };
                core.NavigationStarting += (s, e) =>
                {
                    if (IsAppPage(e.Uri)) return;
                    e.Cancel = true;
                    if (IsLocal(e.Uri))
                        backend.Write($"[{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff}] warn  host: a page tried to replace the window with {Safe(e.Uri)}: kept");
                    else OpenExternal(e.Uri);
                };
                core.DocumentTitleChanged += (s, e) => { Text = string.IsNullOrEmpty(core.DocumentTitle) ? Program.AppName : core.DocumentTitle; };
                web.Source = new Uri(backend.Url);
            }
            catch (WebView2RuntimeNotFoundException)
            {
                var r = MessageBox.Show(this,
                    "The Microsoft Edge WebView2 Runtime is not installed, so Open77 Warden opens in your web browser instead.\n\n" +
                    "Install WebView2 now (recommended)? Choose No to just use the browser.",
                    Program.AppName, MessageBoxButtons.YesNo, MessageBoxIcon.Information);
                if (r == DialogResult.Yes) OpenExternal("https://go.microsoft.com/fwlink/p/?LinkId=2124703");
                BrowserMode();
            }
        }

        /// <summary>One of the app's own addresses on this PC: http://&lt;name&gt;.localhost:&lt;port&gt;/.</summary>
        static bool IsLocal(string uri)
        {
            if (!Uri.TryCreate(uri, UriKind.Absolute, out var u)) return false;
            return u.Scheme == Uri.UriSchemeHttp && u.Host.EndsWith(".localhost", StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>An app page (the window's own pages are all under /__o77w/; the servers' panels live
        /// in their frame, never as the whole window).</summary>
        static bool IsAppPage(string uri)
        {
            if (!IsLocal(uri)) return false;
            var u = new Uri(uri);
            return u.AbsolutePath == "/__o77w" || u.AbsolutePath.StartsWith("/__o77w/", StringComparison.Ordinal);
        }

        /// <summary>An address for the log: no query (a sign-in token never lands in a file).</summary>
        static string Safe(string uri) => (uri ?? "").Split('?')[0];

        void BrowserMode()
        {
            splash.Visible = true;
            splash.BringToFront();
            splash.Status = "Running in your browser";
            splash.Progress = 100;
            splash.Detail = "Keep this window open while you use it: closing it stops Open77 Warden.  " + backend.SafeUrl;
            splash.SetAction("Open in browser", () => OpenExternal(backend.Url));
            OpenExternal(backend.Url);
        }

        static void OpenExternal(string url)
        {
            if (url == null) return;
            if (!url.StartsWith("http://", StringComparison.OrdinalIgnoreCase) && !url.StartsWith("https://", StringComparison.OrdinalIgnoreCase)) return;
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
        }

        static void OpenFolder(string folder)
        {
            try { Process.Start(new ProcessStartInfo("explorer.exe", "\"" + folder + "\"") { UseShellExecute = true }); } catch { }
        }

        void LoadWindowState()
        {
            try
            {
                var p = File.ReadAllText(stateFile).Split(',');
                var r = new Rectangle(int.Parse(p[0]), int.Parse(p[1]), int.Parse(p[2]), int.Parse(p[3]));
                if (Screen.AllScreens.Length > 0 && Array.Exists(Screen.AllScreens, s => s.WorkingArea.IntersectsWith(r)))
                {
                    StartPosition = FormStartPosition.Manual;
                    Bounds = r;
                }
                if (p.Length > 4 && p[4] == "max") WindowState = FormWindowState.Maximized;
            }
            catch { }
        }

        void SaveWindowState()
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(stateFile));
                var r = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
                File.WriteAllText(stateFile, $"{r.X},{r.Y},{r.Width},{r.Height},{(WindowState == FormWindowState.Maximized ? "max" : "normal")}");
            }
            catch { }
        }
    }
}
