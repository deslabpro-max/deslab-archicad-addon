# deslab: native Windows messages for two known startup blockers of our test PID; no mouse or desktop screenshots.
if (-not ('DeslabStartupDialogs' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class DeslabStartupDialogs {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr p);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, EnumProc cb, IntPtr p);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder b, int n);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder b, int n);
    [DllImport("user32.dll", SetLastError=true)] static extern IntPtr SendMessageTimeout(IntPtr h, uint m, IntPtr w, IntPtr l, uint flags, uint timeout, out IntPtr result);
    static string Text(IntPtr h) {var b=new StringBuilder(512); GetWindowText(h,b,512); return b.ToString();}
    public static List<string> Inspect(uint target, bool dismiss) {
        var rows=new List<string>();
        EnumWindows((h,l)=>{
            uint pid; GetWindowThreadProcessId(h,out pid); if(pid!=target) return true;
            var cls=new StringBuilder(128); GetClassName(h,cls,128);
            if(cls.ToString()!="DGModalDlgClass" && cls.ToString()!="#32770") return true;
            string title=Text(h); rows.Add("Dialog: "+title);
            EnumChildWindows(h,(ch,ll)=>{
                string text=Text(ch); if(!dismiss && text.Length>0) rows.Add("  "+text);
                // Cancel keeps the pre-existing recovery files. Never choose Open or Delete.
                if(dismiss && title=="Восстановление Проекта Archicad" && text=="Отменить") {
                    IntPtr result; var ok=SendMessageTimeout(ch,0x00F5,IntPtr.Zero,IntPtr.Zero,2,1500,out result);
                    rows.Add("Recovery cancel: "+(ok!=IntPtr.Zero ? "sent" : "failed "+Marshal.GetLastWin32Error()));
                }
                return true;
            },IntPtr.Zero);
            if(dismiss && title=="Обзор папок") {
                IntPtr result; var ok=SendMessageTimeout(h,0x0010,IntPtr.Zero,IntPtr.Zero,2,1500,out result);
                rows.Add("Folder chooser cancel: "+(ok!=IntPtr.Zero ? "sent" : "failed "+Marshal.GetLastWin32Error()));
            }
            return true;
        },IntPtr.Zero);
        return rows;
    }
}
'@
}
