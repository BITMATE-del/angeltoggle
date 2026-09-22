using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

internal static class Program
{
    private const string ReleaseApi = "https://api.github.com/repos/BITMATE-del/angeltoggle/releases/latest";

    [STAThread]
    private static void Main()
    {
        try
        {
            Run();
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                ex.Message,
                "엔젤토글 실행 오류",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
    }

    private static void Run()
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;

        string root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "AngelToggle"
        );
        string appDir = Path.Combine(root, "app");
        string targetExe = Path.Combine(appDir, "AngelToggle.exe");
        string updateDir = Path.Combine(root, "update");
        string zipPath = Path.Combine(updateDir, "AngelToggle-Windows.zip");
        string stageDir = Path.Combine(root, "launcher_stage");
        string versionFile = Path.Combine(appDir, "version.txt");

        Directory.CreateDirectory(root);
        Directory.CreateDirectory(updateDir);

        string json;
        using (var wc = NewClient())
        {
            json = wc.DownloadString(ReleaseApi);
        }

        string latest = Regex.Match(
            json,
            "\"tag_name\"\s*:\s*\"v?([^\"]+)\"",
            RegexOptions.IgnoreCase
        ).Groups[1].Value;

        string zipUrl = Regex.Match(
            json,
            "\"browser_download_url\"\s*:\s*\"([^\"]*/AngelToggle-Windows\\.zip)\"",
            RegexOptions.IgnoreCase
        ).Groups[1].Value.Replace("\\/","/");

        if (String.IsNullOrWhiteSpace(latest) || String.IsNullOrWhiteSpace(zipUrl))
            throw new Exception("최신 엔젤토글 설치파일을 찾을 수 없습니다.");

        string current = "0.0.0";
        if (File.Exists(versionFile))
        {
            try { current = File.ReadAllText(versionFile).Trim(); } catch { }
        }

        if (!File.Exists(targetExe) || IsNewer(latest, current))
        {
            if (File.Exists(zipPath)) File.Delete(zipPath);

            using (var wc = NewClient())
            {
                wc.DownloadFile(zipUrl, zipPath);
            }

            if (!File.Exists(zipPath) || new FileInfo(zipPath).Length < 5L * 1024L * 1024L)
                throw new Exception("업데이트 파일 다운로드가 정상적으로 완료되지 않았습니다.");

            if (Directory.Exists(stageDir))
                Directory.Delete(stageDir, true);
            Directory.CreateDirectory(stageDir);

            ZipFile.ExtractToDirectory(zipPath, stageDir);

            string source = Path.Combine(stageDir, "AngelToggle");
            string sourceExe = Path.Combine(source, "AngelToggle.exe");
            if (!File.Exists(sourceExe))
                throw new Exception("업데이트 압축파일 안에서 AngelToggle.exe를 찾을 수 없습니다.");

            string oldDir = Path.Combine(root, "app_old");
            if (Directory.Exists(oldDir))
                SafeDeleteDirectory(oldDir);

            if (Directory.Exists(appDir))
            {
                try
                {
                    Directory.Move(appDir, oldDir);
                }
                catch
                {
                    SafeDeleteDirectory(appDir);
                }
            }

            CopyDirectory(source, appDir);
            File.WriteAllText(versionFile, latest);

            SafeDeleteDirectory(stageDir);
            SafeDeleteDirectory(oldDir);
        }

        string vc = Path.Combine(appDir, "vc_redist.x64.exe");
        string vcMarker = Path.Combine(root, "vc_runtime_installed.txt");
        if (File.Exists(vc) && !File.Exists(vcMarker))
        {
            var psi = new ProcessStartInfo
            {
                FileName = vc,
                Arguments = "/install /quiet /norestart",
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = appDir
            };

            using (var p = Process.Start(psi))
            {
                if (p == null) throw new Exception("Visual C++ 런타임 설치를 시작할 수 없습니다.");
                p.WaitForExit(180000);
                int code = p.HasExited ? p.ExitCode : -1;
                if (!p.HasExited)
                {
                    try { p.Kill(); } catch { }
                    throw new Exception("Visual C++ 런타임 설치 시간이 초과되었습니다.");
                }
                if (code != 0 && code != 1638 && code != 3010)
                    throw new Exception("Visual C++ 런타임 설치 실패. 오류코드: " + code);
            }
            File.WriteAllText(vcMarker, "ok");
        }

        if (!File.Exists(targetExe))
            throw new Exception("설치된 AngelToggle.exe를 찾을 수 없습니다.");

        Process.Start(new ProcessStartInfo
        {
            FileName = targetExe,
            WorkingDirectory = appDir,
            UseShellExecute = true
        });
    }

    private static WebClient NewClient()
    {
        var wc = new WebClient();
        wc.Headers.Add("User-Agent", "AngelToggle-Native-Launcher");
        wc.Headers.Add("Accept", "application/vnd.github+json");
        return wc;
    }

    private static bool IsNewer(string a, string b)
    {
        Version va, vb;
        if (!Version.TryParse(Normalize(a), out va)) va = new Version(0,0,0);
        if (!Version.TryParse(Normalize(b), out vb)) vb = new Version(0,0,0);
        return va > vb;
    }

    private static string Normalize(string value)
    {
        if (String.IsNullOrWhiteSpace(value)) return "0.0.0";
        string[] p = value.Trim().TrimStart('v','V').Split('.');
        while (p.Length < 3)
            value += ".0";
        return value.Trim().TrimStart('v','V');
    }

    private static void CopyDirectory(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (string file in Directory.GetFiles(source))
        {
            File.Copy(file, Path.Combine(destination, Path.GetFileName(file)), true);
        }
        foreach (string dir in Directory.GetDirectories(source))
        {
            CopyDirectory(dir, Path.Combine(destination, Path.GetFileName(dir)));
        }
    }

    private static void SafeDeleteDirectory(string path)
    {
        if (!Directory.Exists(path)) return;
        for (int i = 0; i < 5; i++)
        {
            try
            {
                Directory.Delete(path, true);
                return;
            }
            catch
            {
                Thread.Sleep(500);
            }
        }
    }
}
