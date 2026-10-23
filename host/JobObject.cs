// A Windows job object with KILL_ON_JOB_CLOSE: closing the handle ends every process in it (the app's
// server and what it started never outlive the window).  Shared by the Studio and Open77 Warden.
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace Fivem2Open77.Studio
{
    /// <summary>Windows job object with KILL_ON_JOB_CLOSE: closing the handle ends every process in it.</summary>
    sealed class JobObject : IDisposable
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr a, string name);
        [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int cls, IntPtr info, uint len);
        [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);

        [StructLayout(LayoutKind.Sequential)]
        struct BASIC { public long PerProcessUserTimeLimit, PerJobUserTimeLimit; public uint LimitFlags; public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize; public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass, SchedulingClass; }
        [StructLayout(LayoutKind.Sequential)]
        struct IO { public ulong a, b, c, d, e, f; }
        [StructLayout(LayoutKind.Sequential)]
        struct EXTENDED { public BASIC Basic; public IO Io; public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed; }

        IntPtr handle;

        public JobObject()
        {
            handle = CreateJobObject(IntPtr.Zero, null);
            var info = new EXTENDED();
            info.Basic.LimitFlags = 0x2000;          // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            int len = Marshal.SizeOf(typeof(EXTENDED));
            IntPtr p = Marshal.AllocHGlobal(len);
            try
            {
                Marshal.StructureToPtr(info, p, false);
                SetInformationJobObject(handle, 9, p, (uint)len);   // JobObjectExtendedLimitInformation
            }
            finally { Marshal.FreeHGlobal(p); }
        }

        public void Add(Process p) { if (handle != IntPtr.Zero) AssignProcessToJobObject(handle, p.Handle); }

        public void Dispose()
        {
            if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; }
        }
    }
}
