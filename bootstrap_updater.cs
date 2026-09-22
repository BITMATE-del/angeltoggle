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
    private static Form progressForm;
    private static Label statusLabel;
    private static ProgressBar progressBar;

    [STAThread]
    private static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        try
        {
            CreateProgressWindow();
            progressForm.Show();
            Application.DoEvents();
            Run();
        }
        catch (Exception ex)
        {
            try { if (progressForm != null) progressForm.Hide(); } catch { }
            MessageBox.Show(
                ex.Message,
                "엔젤토글 실행 오류",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
        finally
        {
            try { if (progressForm != null) progressForm.Close(); } catch { }
        }
    }

    private static void CreateProgressWindow()
    {
        progressForm = new Form();
        progressForm.Text = "엔젤토글 시작";
        progressForm.Width = 520;
        progressForm.Height = 165;
        progressForm.StartPosition = FormStartPosition.CenterScreen;
        progressForm.FormBorderStyle = FormBorderStyle.FixedDialog;
        progressForm.MaximizeBox = false;
        progressForm.MinimizeBox = false;
        progressForm.ControlBox = false;
        progressForm.TopMost = true;

        statusLabel = new Label();
        statusLabel.Left = 22;
        statusLabel.Top = 20;
        statusLabel.Width = 455;
        statusLabel.Height = 28;
        statusLabel.Text = "엔젤토글 시작 준비 중...";

        progressBar = new ProgressBar();
        progressBar.Left = 22;
        progressBar.Top = 58;
        progressBar.Width = 455;
        progressBar.Height = 24;
        progressBar.Minimum = 0;
        progressBar.Maximum = 100;
        progressBar.Value = 0;

        var hint = new Label();
        hint.Left = 22;
        hint.Top = 92;
        hint.Width = 455;
        hint.Height = 28;
        hint.Text = "창을 닫지 마세요. 업데이트가 있으면 자동으로 설치합니다.";

        progressForm.Controls.Add(statusLabel);
        progressForm.Controls.Add(progressBar);
        progressForm.Controls.Add(hint);
    }

    private static void SetStatus(string text, int percent)
    {
        statusLabel.Text = text;
        if (percent < 0) percent = 0;
        if (percent > 100) percent = 100;
        progressBar.Value = percent;
        Application.DoEvents();
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

        SetStatus("최신 버전 확인 중...", 5);

        string json;
        using (var wc = new WebClient())
        {
            wc.Headers.Add("User-Agent", "AngelToggle-Native-Launcher");
            wc.Headers.Add("Accept", "application/vnd.github+json");
            json = wc.DownloadString(ReleaseApi);
        }

        string latest = Regex.Match(
            json,
            @"""tag_name""\s*:\s*""v?([^""]+)""",
            RegexOptions.IgnoreCase
        ).Groups[1].Value;

        string zipUrl = Regex.Match(
            json,
            @"""browser_download_url""\s*:\s*""([^""]*/AngelToggle-Windows\.zip)""",
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
            SetStatus("새 버전 " + latest + " 다운로드 준비 중...", 10);

            if (File.Exists(zipPath)) File.Delete(zipPath);
            DownloadWithProgress(zipUrl, zipPath);

            if (!File.Exists(zipPath) || new FileInfo(zipPath).Length < 5L * 1024L * 1024L)
                throw new Exception("업데이트 파일 다운로드가 정상적으로 완료되지 않았습니다.");

            SetStatus("업데이트 압축 해제 중...", 72);

            if (Directory.Exists(stageDir))
                SafeDeleteDirectory(stageDir);
            Directory.CreateDirectory(stageDir);

            ZipFile.ExtractToDirectory(zipPath, stageDir);

            string source = Path.Combine(stageDir, "AngelToggle");
            string sourceExe = Path.Combine(source, "AngelToggle.exe");
            if (!File.Exists(sourceExe))
                throw new Exception("업데이트 압축파일 안에서 AngelToggle.exe를 찾을 수 없습니다.");

            SetStatus("프로그램 파일 설치 중...", 82);

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
        else
        {
            SetStatus("최신 버전 확인 완료", 80);
        }

        string vc = Path.Combine(appDir, "vc_redist.x64.exe");
        string vcMarker = Path.Combine(root, "vc_runtime_installed.txt");
        if (File.Exists(vc) && !File.Exists(vcMarker))
        {
            SetStatus("Windows 필수 런타임 설치 중...", 88);

            var psi = new ProcessStartInfo();
            psi.FileName = vc;
            psi.Arguments = "/install /quiet /norestart";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.WorkingDirectory = appDir;

            using (var p = Process.Start(psi))
            {
                if (p == null)
                    throw new Exception("Visual C++ 런타임 설치를 시작할 수 없습니다.");

                int waited = 0;
                while (!p.HasExited && waited < 180000)
                {
                    Thread.Sleep(250);
                    waited += 250;
                    Application.DoEvents();
                }

                if (!p.HasExited)
                {
                    try { p.Kill(); } catch { }
                    throw new Exception("Visual C++ 런타임 설치 시간이 초과되었습니다.");
                }

                int code = p.ExitCode;
                if (code != 0 && code != 1638 && code != 3010)
                    throw new Exception("Visual C++ 런타임 설치 실패. 오류코드: " + code);
            }

            File.WriteAllText(vcMarker, "ok");
        }

        if (!File.Exists(targetExe))
            throw new Exception("설치된 AngelToggle.exe를 찾을 수 없습니다.");

        SetStatus("엔젤토글 실행 중...", 100);
        Thread.Sleep(500);

        Process.Start(new ProcessStartInfo
        {
            FileName = targetExe,
            WorkingDirectory = appDir,
            UseShellExecute = true
        });
    }

    private static void DownloadWithProgress(string url, string target)
    {
        var request = (HttpWebRequest)WebRequest.Create(url);
        request.UserAgent = "AngelToggle-Native-Launcher";
        request.AllowAutoRedirect = true;

        using (var response = (HttpWebResponse)request.GetResponse())
        using (var input = response.GetResponseStream())
        using (var output = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None))
        {
            long total = response.ContentLength;
            long received = 0;
            byte[] buffer = new byte[1024 * 1024];

            while (true)
            {
                int read = input.Read(buffer, 0, buffer.Length);
                if (read <= 0) break;

                output.Write(buffer, 0, read);
                received += read;

                int percent = 10;
                if (total > 0)
                    percent = 10 + (int)((received * 60L) / total);

                long mb = received / (1024L * 1024L);
                long totalMb = total > 0 ? total / (1024L * 1024L) : 0;
                string text = total > 0
                    ? "업데이트 다운로드 중... " + mb + "MB / " + totalMb + "MB"
                    : "업데이트 다운로드 중... " + mb + "MB";

                SetStatus(text, percent);
            }
        }
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
        value = value.Trim().TrimStart('v','V');
        string[] p = value.Split('.');
        if (p.Length == 1) return value + ".0.0";
        if (p.Length == 2) return value + ".0";
        return value;
    }

    private static void CopyDirectory(string source, string destination)
    {
        Directory.CreateDirectory(destination);

        foreach (string file in Directory.GetFiles(source))
            File.Copy(file, Path.Combine(destination, Path.GetFileName(file)), true);

        foreach (string dir in Directory.GetDirectories(source))
            CopyDirectory(dir, Path.Combine(destination, Path.GetFileName(dir)));
    }

    private static void SafeDeleteDirectory(string path)
    {
        if (!Directory.Exists(path)) return;

        for (int i = 0; i < 8; i++)
        {
            try
            {
                Directory.Delete(path, true);
                return;
            }
            catch
            {
                Thread.Sleep(500);
                Application.DoEvents();
            }
        }
    }
}
