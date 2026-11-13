# deslab: separate background watchdog for Archicad service windows (works without the caller).
# Every second lists the visible top-level windows of every Archicad.exe and acts by title.
# Never presses delete buttons, never touches file save/open dialogs. Log: %TEMP%\deslab-ac-watchdog.log
param([string]$Log = "$env:TEMP\deslab-ac-watchdog.log", [string]$PidFile = "$env:TEMP\deslab-ac-watchdog.pid", [int]$MaxMinutes = 240)
Add-Type -TypeDefinition @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class WD {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  static string Cls(IntPtr h){ var c=new StringBuilder(128); GetClassName(h,c,128); return c.ToString(); }
  static string Txt(IntPtr h){ var t=new StringBuilder(512); GetWindowText(h,t,512); return t.ToString(); }
  // one pass; returns log lines
  public static List<string> Pass(HashSet<uint> pids, uint ourPid, bool stopFlag) {
    var r = new List<string>();
    EnumWindows((h, l) => {
      uint p; GetWindowThreadProcessId(h, out p); if (!pids.Contains(p)) return true;
      string c = Cls(h), t = Txt(h);
      bool dlg = (c == "#32770" || c == "DGModalDlgClass");
      if (!IsWindowVisible(h) && c != "#32770") return true;   // hidden folder dialog is #32770 and invisible to UIA
      if (!dlg) return true;
      var btns = new List<KeyValuePair<IntPtr,string>>(); var stat = new List<string>();
      EnumChildWindows(h, (ch, l2) => { string cc = Cls(ch), tt = Txt(ch);
        if (cc == "Button" && tt.Length > 0) btns.Add(new KeyValuePair<IntPtr,string>(ch, tt));
        else if (cc == "Static" && tt.Length > 0) stat.Add(tt); return true; }, IntPtr.Zero);
      string bl = ""; foreach (var b in btns) bl += "[" + b.Value + "]";
      string info = "pid=" + p + " class=" + c + " title='" + t + "' buttons=" + bl;
      // only the Archicad that ac_start launched (pid file); a window of any other Archicad (the user's) is only logged
      if (p != ourPid) { r.Add("UNKNOWN (not our pid, left alone) " + info); return true; }
      if (t.Contains("Обзор папок")) { PostMessage(h, 0x0010, IntPtr.Zero, IntPtr.Zero); r.Add("ACTION WM_CLOSE " + info); }
      else if (t.Contains("Восстановление Проекта")) {
        bool done = false;
        foreach (var b in btns) if (b.Value == "Продолжить") { SendMessage(b.Key, 0x00F5, IntPtr.Zero, IntPtr.Zero); done = true; r.Add("ACTION click Продолжить " + info); break; }
        if (!done) foreach (var b in btns) if (b.Value == "Отменить") { SendMessage(b.Key, 0x00F5, IntPtr.Zero, IntPtr.Zero); done = true; r.Add("ACTION click Отменить " + info); break; }
        if (!done) r.Add("UNKNOWN " + info);
      }
      else if (t.Contains("Сохранение Изменений")) {
        // only our own test process; Cancel during start, Don't save when ac_stop raised the flag
        string want = (p == ourPid) ? (stopFlag ? "Не сохранять" : "Отменить") : null;
        bool done = false;
        if (want != null) foreach (var b in btns) if (b.Value == want) { SendMessage(b.Key, 0x00F5, IntPtr.Zero, IntPtr.Zero); done = true; r.Add("ACTION click " + want + " (save dialog, our pid) " + info); break; }
        if (!done) r.Add("UNKNOWN (not our pid, left alone) " + info);
      }
      else if (t.Contains("Ошибка") && string.Join(" ", stat).Contains("Library not loaded")) {
        foreach (var b in btns) if (b.Value == "OK") { SendMessage(b.Key, 0x00F5, IntPtr.Zero, IntPtr.Zero); r.Add("ACTION click OK (Library not loaded) " + info); break; }
      }
      else if (t.Contains("Отсутствующие") || t.Contains("Библиотек") || t.Contains("Менеджер библиотек")) {
        bool done = false;
        foreach (var b in btns) if (b.Value == "OK" || b.Value == "ОК" || b.Value == "Закрыть" || b.Value == "Продолжить") { SendMessage(b.Key, 0x00F5, IntPtr.Zero, IntPtr.Zero); done = true; r.Add("ACTION click " + b.Value + " " + info); break; }
        if (!done) { PostMessage(h, 0x0010, IntPtr.Zero, IntPtr.Zero); r.Add("ACTION WM_CLOSE " + info); }
      }
      else { string s = ""; foreach (var x in stat) s += x + " | "; r.Add("UNKNOWN " + info + " text=" + s); }
      return true; }, IntPtr.Zero);
    return r;
  }
}
"@
$PID | Set-Content $PidFile
$end = (Get-Date).AddMinutes($MaxMinutes); $seen = @{}
"{0} watchdog started pid {1}" -f (Get-Date -Format HH:mm:ss.f), $PID | Add-Content $Log -Encoding UTF8
while ((Get-Date) -lt $end) {
  $ids = [System.Collections.Generic.HashSet[uint32]]::new()
  Get-Process archicad -ErrorAction SilentlyContinue | ForEach-Object { [void]$ids.Add([uint32]$_.Id) }
  if ($ids.Count -gt 0) {
    $our = 0; if (Test-Path "$env:TEMP\deslab-ac.pid") { $our = [uint32](Get-Content "$env:TEMP\deslab-ac.pid") }
    $flag = Test-Path "$env:TEMP\deslab-ac-stop.flag"
    foreach ($line in [WD]::Pass($ids, $our, $flag)) {
      $key = $line; $now = Get-Date
      # unknown windows logged once per 30 s, actions every time (a repeat means the click did not work)
      if ($line.StartsWith("UNKNOWN") -and $seen[$key] -and ($now - $seen[$key]).TotalSeconds -lt 30) { continue }
      $seen[$key] = $now
      "{0} {1}" -f $now.ToString("HH:mm:ss.f"), $line | Add-Content $Log -Encoding UTF8
    }
  }
  Start-Sleep -Milliseconds 1000
}
