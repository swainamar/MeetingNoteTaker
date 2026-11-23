// MeetingNoteTaker.exe - starts Meeting Note-Taker with the Python runtime bundled in
// MeetingNoteTaker\runtime. Python runs inside this process, so the taskbar, Start menu and
// pinned icons all belong to MeetingNoteTaker.exe (pin it like Word or Excel).
//
//   MeetingNoteTaker.exe                 start the app
//   MeetingNoteTaker.exe --compact       app options are passed to app.py
//   MeetingNoteTaker.exe script.py ...   run another script with the bundled Python
//
// Build: build\build.ps1 (uses the C# compiler that ships with Windows).
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("Meeting Note-Taker")]
[assembly: AssemblyProduct("Meeting Note-Taker")]
[assembly: AssemblyDescription("Records meetings and writes notes - runs locally")]
[assembly: AssemblyCompany("swainamar")]
[assembly: AssemblyCopyright("MIT License")]
[assembly: AssemblyVersion("2.2.0.0")]
[assembly: AssemblyFileVersion("2.2.0.0")]

static class Program
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetDllDirectoryW(string path);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibraryW(string path);

    [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int PyMainFn(int argc, IntPtr argv);

    const string Title = "Meeting Note-Taker";

    static int Fail(string msg)
    {
        MessageBox.Show(msg, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
        return 1;
    }

    [STAThread]
    static int Main(string[] args)
    {
        string exe = Assembly.GetExecutingAssembly().Location;
        string root = Path.GetDirectoryName(exe);
        string app = Path.Combine(root, "MeetingNoteTaker");
        string runtime = Path.Combine(app, "runtime");
        string dll = Path.Combine(runtime, "python311.dll");

        if (!File.Exists(Path.Combine(app, "app.py")))
            return Fail("The \"MeetingNoteTaker\" folder must stay next to MeetingNoteTaker.exe.\n\n" +
                        "Extract the whole ZIP file, then start MeetingNoteTaker.exe from the extracted folder.");
        if (!File.Exists(dll))
            return Fail("The bundled Python runtime is missing:\n" + runtime + "\n\n" +
                        "Download the portable ZIP again, or build it with build\\build.ps1.");

        // Only the bundled runtime may be used: ignore any Python settings of this PC.
        foreach (string v in new[] { "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE" })
            Environment.SetEnvironmentVariable(v, null);
        Environment.SetEnvironmentVariable("PYTHONUTF8", "1");
        Directory.SetCurrentDirectory(app);
        SetDllDirectoryW(runtime);

        IntPtr py = LoadLibraryW(dll);
        if (py == IntPtr.Zero)
            return Fail("Could not load the Python runtime (error " + Marshal.GetLastWin32Error() + ").\n\n" +
                        "Install the Microsoft Visual C++ Redistributable (x64) and try again.");
        IntPtr fn = GetProcAddress(py, "Py_Main");
        if (fn == IntPtr.Zero)
            return Fail("The Python runtime is damaged (Py_Main not found).");
        var pyMain = (PyMainFn)Marshal.GetDelegateForFunctionPointer(fn, typeof(PyMainFn));

        // A first argument that is a script or a single-dash Python option is passed to Python as-is
        // (used by the app to start Voice to Prompt); anything else is an option for app.py.
        bool passThrough = args.Length > 0 &&
            (args[0].EndsWith(".py", StringComparison.OrdinalIgnoreCase) ||
             (args[0].StartsWith("-") && !args[0].StartsWith("--")));
        var argv = new List<string> { exe };
        if (!passThrough)
            argv.Add(Path.Combine(app, "app.py"));
        argv.AddRange(args);

        IntPtr block = Marshal.AllocHGlobal(IntPtr.Size * (argv.Count + 1));
        for (int i = 0; i < argv.Count; i++)
            Marshal.WriteIntPtr(block, i * IntPtr.Size, Marshal.StringToHGlobalUni(argv[i]));
        Marshal.WriteIntPtr(block, argv.Count * IntPtr.Size, IntPtr.Zero);
        return pyMain(argv.Count, block);
    }
}
