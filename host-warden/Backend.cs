// The Warden app's local server: runtime\python\python.exe -m fivem2open77.warden serve, kept in a
// job object so it (and WolvenKit under it, while it reads the 3D city) never outlives the window.
// Its output goes to %LOCALAPPDATA%\open77-warden\logs\warden-<when>.log.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Fivem2Open77.Studio;

namespace Open77.Warden
{
    sealed class Backend : IDisposable
    {
        public readonly string Home;
        public readonly string Python;
        public readonly string Workspace;
        public string LogFile { get; private set; }
        /// <summary>The home page's sign-in address (it carries the session token: never shown or logged).</summary>
        public string Url { get; private set; }
        public int Port { get; private set; }
        public Process Proc { get; private set; }
        public event Action<int> Exited;

        readonly JobObject job = new JobObject();
        StreamWriter log;
        bool disposing;

        public Backend(string home)
        {
            Home = home;
            Python = Path.Combine(home, "runtime", "python", "python.exe");
            Workspace = Program.DataDir;
        }

        /// <summary>The address without its query (no token): for messages and the log.</summary>
        public string SafeUrl => Url == null ? "" : Url.Split('?')[0];

        /// <summary>Start the server and wait until it listens.  Throws with the log's last lines on failure.</summary>
        public void Start(TimeSpan timeout)
        {
            if (!File.Exists(Python))
                throw new FileNotFoundException("The app is incomplete: " + Python + " is missing. Reinstall " + Program.AppName + ".");
            Directory.CreateDirectory(Path.Combine(Workspace, "logs"));
            CleanOldLogs(Path.Combine(Workspace, "logs"));
            LogFile = Path.Combine(Workspace, "logs", "warden-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
            log = new StreamWriter(new FileStream(LogFile, FileMode.Create, FileAccess.Write, FileShare.ReadWrite), new UTF8Encoding(false)) { AutoFlush = true };
            Write($"[{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff}] info  host: {Program.AppName} {Program.Version} window starting the server ({Home})");
            string ready = Path.Combine(Path.GetTempPath(), "o77w-ready-" + Guid.NewGuid().ToString("N") + ".json");
            var psi = new ProcessStartInfo(Python,
                "-X utf8 -m fivem2open77.warden serve --port 0 --workspace \"" + Workspace + "\" --ready-file \"" + ready + "\"")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                WorkingDirectory = Home,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            psi.EnvironmentVariables["F2O77_HOME"] = Home;
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            psi.EnvironmentVariables["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1";
            psi.EnvironmentVariables["DOTNET_NOLOGO"] = "1";
            Proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
            Proc.OutputDataReceived += (s, e) => { if (e.Data != null) Write(e.Data); };
            Proc.ErrorDataReceived += (s, e) => { if (e.Data != null) Write(e.Data); };
            Proc.Exited += (s, e) => { if (!disposing) Exited?.Invoke(SafeExitCode()); };
            Proc.Start();
            job.Add(Proc);
            Proc.BeginOutputReadLine();
            Proc.BeginErrorReadLine();
            var sw = Stopwatch.StartNew();
            while (sw.Elapsed < timeout)
            {
                if (File.Exists(ready))
                {
                    try
                    {
                        string json = File.ReadAllText(ready);
                        var m = Regex.Match(json, "\"url\"\\s*:\\s*\"([^\"]+)\"");
                        var p = Regex.Match(json, "\"port\"\\s*:\\s*(\\d+)");
                        if (m.Success && p.Success)
                        {
                            Url = Regex.Unescape(m.Groups[1].Value);
                            Port = int.Parse(p.Groups[1].Value);
                            try { File.Delete(ready); } catch { }
                            Write($"[{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff}] info  host: the server listens: {SafeUrl} (in {sw.Elapsed.TotalSeconds:0.0} s)");
                            return;
                        }
                    }
                    catch (IOException) { }
                }
                if (Proc.HasExited)
                    throw new InvalidOperationException("The Warden app's server stopped while starting (exit " + SafeExitCode() + ").\n\n" + Tail());
                Thread.Sleep(100);
            }
            throw new TimeoutException("The Warden app's server did not start in " + (int)timeout.TotalSeconds + " s.\n\n" + Tail());
        }

        int SafeExitCode() { try { return Proc.ExitCode; } catch { return -1; } }

        public void Write(string line)
        {
            try { lock (this) log?.WriteLine(line); } catch { }
        }

        public string Tail(int lines = 12)
        {
            try
            {
                using (var fs = new FileStream(LogFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var r = new StreamReader(fs))
                {
                    var all = r.ReadToEnd().Split('\n');
                    int from = Math.Max(0, all.Length - lines);
                    return string.Join("\n", all, from, all.Length - from).Trim();
                }
            }
            catch { return "(no log)"; }
        }

        static void CleanOldLogs(string dir)
        {
            try
            {
                foreach (var f in new DirectoryInfo(dir).GetFiles("warden-*.log"))
                    if (f.LastWriteTime < DateTime.Now.AddDays(-14)) f.Delete();
            }
            catch { }
        }

        public void Dispose()
        {
            disposing = true;
            Write($"[{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff}] info  host: the window closed: the server stops");
            try { if (Proc != null && !Proc.HasExited) Proc.Kill(); } catch { }
            job.Dispose();          // ends anything still in the job (WolvenKit)
            try { log?.Dispose(); } catch { }
        }
    }
}
