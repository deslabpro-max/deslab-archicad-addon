// deslab: the "deslab Obmer" palette, see DeslabObmerPalette.hpp and README.md.
// Non-ASCII text is written as UTF-8 escapes: the CI compiler reads sources in code page 1252.

#include "DeslabObmerPalette.hpp"
#include "ResourceIds.hpp"
#include "IBinaryChannelUtilities.hpp"
#include "MessageLoopExecutor.hpp"
#include "OnExit.hpp"

#include <memory>

#if defined (WINDOWS)
#pragma warning (push)
#pragma warning (disable : 4995 4091)
#include <shlobj.h>
#pragma warning (pop)
#pragma comment (lib, "shell32.lib")
#pragma comment (lib, "ole32.lib")
#endif

const GS::Guid                      DeslabObmerPalette::paletteGuid ("{8C1F4E52-3B7D-4A61-9E2C-5D0A7B9F1E34}");
GS::Ref<DeslabObmerPalette>         DeslabObmerPalette::instance;
std::atomic<bool>                   DeslabObmerPalette::quitting (false);
std::mutex                          DeslabObmerPalette::postedTasksMutex;
std::vector<GS::RunnableTask>       DeslabObmerPalette::postedTasks;
std::vector<GS::RunnableTask>       DeslabObmerPalette::finishedTasks;

namespace {

GS::UniString U (const char* utf8)
{
    return GS::UniString (utf8, CC_UTF8);
}

// The models offered in the palette: label, then the argument for the bridge ("" = the profile's default model,
// "-" = build without the AI).
struct ModelChoice { const char* label; const char* id; };
const ModelChoice modelChoices[] = {
    { "Claude Sonnet 5.5 (\xD0\xBF\xD0\xBE \xD1\x83\xD0\xBC\xD0\xBE\xD0\xBB\xD1\x87\xD0\xB0\xD0\xBD\xD0\xB8\xD1\x8E)", "" },
    { "Claude Opus 5.5 (\xD1\x81\xD0\xBB\xD0\xBE\xD0\xB6\xD0\xBD\xD1\x8B\xD0\xB5 \xD1\x81\xD0\xBB\xD1\x83\xD1\x87\xD0\xB0\xD0\xB8, \xD0\xB4\xD0\xBE\xD1\x80\xD0\xBE\xD0\xB6\xD0\xB5)", "anthropic/claude-opus-5.5" },
    { "\xD0\x91\xD0\xB5\xD0\xB7 \xD0\x98\xD0\x98", "-" },
};

// Decodes the complete UTF-8 characters of pending and keeps an incomplete tail for the next read, so a Cyrillic
// letter split between two reads of the pipe is not lost.
GS::UniString TakeUTF8 (std::string& pending)
{
    size_t end = pending.size ();
    size_t back = 0;
    while (back < 4 && back < end && (static_cast<unsigned char> (pending[end - 1 - back]) & 0xC0) == 0x80) {
        ++back;
    }
    if (back < end) {
        const unsigned char lead = static_cast<unsigned char> (pending[end - 1 - back]);
        const size_t need = lead >= 0xF0 ? 4 : lead >= 0xE0 ? 3 : lead >= 0xC0 ? 2 : 1;
        if (need > back + 1) {
            end -= back + 1;
        }
    }
    GS::UniString text (pending.c_str (), static_cast<GS::USize> (end), CC_UTF8);
    pending.erase (0, end);
    return text;
}

#if defined (WINDOWS)
GS::UniString FromWide (const wchar_t* chars)
{
    return GS::UniString (reinterpret_cast<const GS::UniChar::Layout*> (chars), static_cast<USize> (wcslen (chars)));
}

GS::UniString EnvironmentValue (const wchar_t* name)
{
    wchar_t buffer[4096];
    const DWORD length = GetEnvironmentVariableW (name, buffer, 4096);
    return (length > 0 && length < 4096) ? FromWide (buffer) : GS::UniString ();
}

bool FileExists (const GS::UniString& path)
{
    const DWORD attributes = GetFileAttributesW (reinterpret_cast<const wchar_t*> (path.ToUStr ().Get ()));
    return attributes != INVALID_FILE_ATTRIBUTES;
}
#else
bool FileExists (const GS::UniString&)
{
    return false;
}
#endif

GS::UniString PythonIn (const GS::UniString& bridgeFolder)
{
#if defined (WINDOWS)
    return bridgeFolder + "\\.venv\\Scripts\\python.exe";
#else
    return bridgeFolder + "/.venv/bin/python";
#endif
}

}   // namespace

DeslabObmerPalette::DeslabObmerPalette ()
    : DG::Palette (ACAPI_GetOwnResModule (), ID_DESLAB_OBMER_PALETTE, ACAPI_GetOwnResModule (), paletteGuid)
    , fileLabel (GetReference (), 1)
    , fileEdit (GetReference (), 2)
    , browseButton (GetReference (), 3)
    , pdfLabel (GetReference (), 4)
    , pdfEdit (GetReference (), 5)
    , pdfButton (GetReference (), 6)
    , pageLabel (GetReference (), 7)
    , pageEdit (GetReference (), 8)
    , modelLabel (GetReference (), 9)
    , modelPopUp (GetReference (), 10)
    , buildButton (GetReference (), 11)
    , stopButton (GetReference (), 12)
    , statusText (GetReference (), 13)
    , separator (GetReference (), 14)
    , logLabel (GetReference (), 15)
    , chatLabel (GetReference (), 16)
    , messageEdit (GetReference (), 17)
    , sendButton (GetReference (), 18)
    , logEdit (GetReference (), DG::Rect (8, 190, 392, 410), DG::MultiLineEdit::VScroll, DG::EditControl::Frame,
               DG::EditControl::Update, DG::EditControl::ReadOnly)
    , chatEdit (GetReference (), DG::Rect (8, 436, 392, 556), DG::MultiLineEdit::VScroll, DG::EditControl::Frame,
                DG::EditControl::Update, DG::EditControl::ReadOnly)
{
    Attach (*this);
    browseButton.Attach (*this);
    pdfButton.Attach (*this);
    buildButton.Attach (*this);
    stopButton.Attach (*this);
    sendButton.Attach (*this);
    InitTexts ();
    logEdit.Show ();
    chatEdit.Show ();
    UpdateButtons ();
    BeginEventProcessing ();
}

DeslabObmerPalette::~DeslabObmerPalette ()
{
    EndEventProcessing ();
}

void DeslabObmerPalette::InitTexts ()
{
    SetTitle (U ("deslab \xD0\x9E\xD0\xB1\xD0\xBC\xD0\xB5\xD1\x80"));
    fileLabel.SetText (U ("\xD0\xA1\xD0\xBA\xD0\xB0\xD0\xBD E57 (\xD0\xB8\xD0\xBB\xD0\xB8 \xD0\xB8\xD0\xBC\xD1\x8F \xD0\xBF\xD1\x80\xD0\xB5\xD1\x81\xD0\xB5\xD1\x82\xD0\xB0, \xD0\xBD\xD0\xB0\xD0\xBF\xD1\x80\xD0\xB8\xD0\xBC\xD0\xB5\xD1\x80 kv94):"));
    browseButton.SetText (U ("\xD0\x9E\xD0\xB1\xD0\xB7\xD0\xBE\xD1\x80\xE2\x80\xA6"));
    pdfLabel.SetText (U ("PDF \xD0\xB4\xD0\xBB\xD1\x8F \xD0\x98\xD0\x98, \xD0\xBD\xD0\xB5\xD0\xBE\xD0\xB1\xD1\x8F\xD0\xB7\xD0\xB0\xD1\x82\xD0\xB5\xD0\xBB\xD1\x8C\xD0\xBD\xD0\xBE:"));
    pdfButton.SetText (U ("PDF\xE2\x80\xA6"));
    pageLabel.SetText (U ("\xD1\x81\xD1\x82\xD1\x80."));
    pageEdit.SetText ("1");
    modelLabel.SetText (U ("\xD0\x9C\xD0\xBE\xD0\xB4\xD0\xB5\xD0\xBB\xD1\x8C:"));
    for (const ModelChoice& choice : modelChoices) {
        modelPopUp.AppendItem ();
        modelPopUp.SetItemText (DG::PopUp::BottomItem, U (choice.label));
    }
    modelPopUp.SelectItem (1);
    buildButton.SetText (U ("\xD0\x9F\xD0\xBE\xD1\x81\xD1\x82\xD1\x80\xD0\xBE\xD0\xB8\xD1\x82\xD1\x8C"));
    stopButton.SetText (U ("\xD0\x9E\xD1\x81\xD1\x82\xD0\xB0\xD0\xBD\xD0\xBE\xD0\xB2\xD0\xB8\xD1\x82\xD1\x8C"));
    logLabel.SetText (U ("\xD0\xA5\xD0\xBE\xD0\xB4 \xD1\x81\xD0\xB1\xD0\xBE\xD1\x80\xD0\xBA\xD0\xB8, \xD0\xBD\xD0\xB0\xD0\xB9\xD0\xB4\xD0\xB5\xD0\xBD\xD0\xBD\xD0\xBE\xD0\xB5 \xD0\x98\xD0\x98 \xD0\xB8 \xD0\xB2\xD0\xBE\xD0\xBF\xD1\x80\xD0\xBE\xD1\x81\xD1\x8B:"));
    chatLabel.SetText (U ("\xD0\xA7\xD0\xB0\xD1\x82 \xD1\x81 \xD0\xBC\xD0\xBE\xD0\xB4\xD0\xB5\xD0\xBB\xD1\x8C\xD1\x8E \xD0\xBE \xD0\xBF\xD0\xBE\xD1\x81\xD0\xBB\xD0\xB5\xD0\xB4\xD0\xBD\xD0\xB5\xD0\xB9 \xD1\x81\xD0\xB1\xD0\xBE\xD1\x80\xD0\xBA\xD0\xB5:"));
    sendButton.SetText (U ("\xD0\x9E\xD1\x82\xD0\xBF\xD1\x80\xD0\xB0\xD0\xB2\xD0\xB8\xD1\x82\xD1\x8C"));
    SetStatus (U ("\xD0\x93\xD0\xBE\xD1\x82\xD0\xBE\xD0\xB2\xD0\xBE \xD0\xBA \xD1\x80\xD0\xB0\xD0\xB1\xD0\xBE\xD1\x82\xD0\xB5"));
}

bool DeslabObmerPalette::HasInstance ()
{
    return instance != nullptr;
}

DeslabObmerPalette& DeslabObmerPalette::Instance ()
{
    if (!HasInstance ()) {
        instance = new DeslabObmerPalette ();
    }
    return *instance;
}

void DeslabObmerPalette::Show ()
{
    DG::Palette::Show ();
}

void DeslabObmerPalette::Hide ()
{
    DG::Palette::Hide ();
}

bool DeslabObmerPalette::IsRunning ()
{
    return process.IsValid ();
}

void DeslabObmerPalette::SetStatus (const GS::UniString& text)
{
    statusText.SetText (text);
}

void DeslabObmerPalette::UpdateButtons ()
{
    const bool running = IsRunning ();
    buildButton.SetStatus (!running);
    sendButton.SetStatus (!running);
    stopButton.SetStatus (running);
}

void DeslabObmerPalette::AppendOutput (Job target, const GS::UniString& text)
{
    if (text.IsEmpty ()) {
        return;
    }
    DG::MultiLineEdit& edit = (target == Job::Chat) ? chatEdit : logEdit;
    GS::UniString all = edit.GetText ();
    all += text;
    edit.SetText (all);
    const Int32 length = static_cast<Int32> (all.GetLength ());
    edit.SetSelection (DG::CharRange (length, length));   // keeps the newest lines in view
}

void DeslabObmerPalette::JobFinished (int exitCode)
{
    const Job finished = job;
    job = Job::None;
    process = {};
    if (finished == Job::Build) {
        SetStatus (exitCode == 0 ? U ("\xD0\xA1\xD0\xB1\xD0\xBE\xD1\x80\xD0\xBA\xD0\xB0 \xD0\xB3\xD0\xBE\xD1\x82\xD0\xBE\xD0\xB2\xD0\xB0") : U ("\xD0\xA1\xD0\xB1\xD0\xBE\xD1\x80\xD0\xBA\xD0\xB0 \xD1\x81 \xD0\xBE\xD1\x88\xD0\xB8\xD0\xB1\xD0\xBA\xD0\xBE\xD0\xB9, \xD1\x81\xD0\xBC. \xD1\x85\xD0\xBE\xD0\xB4 \xD1\x81\xD0\xB1\xD0\xBE\xD1\x80\xD0\xBA\xD0\xB8"));
    } else if (finished == Job::Chat) {
        AppendOutput (Job::Chat, "\n");
        SetStatus (exitCode == 0 ? U ("\xD0\x9E\xD1\x82\xD0\xB2\xD0\xB5\xD1\x82 \xD0\xBF\xD0\xBE\xD0\xBB\xD1\x83\xD1\x87\xD0\xB5\xD0\xBD") : U ("\xD0\x9E\xD1\x88\xD0\xB8\xD0\xB1\xD0\xBA\xD0\xB0 \xD1\x87\xD0\xB0\xD1\x82\xD0\xB0"));
    }
    UpdateButtons ();
}

// deslab: the bridge lives in "Arkhikad\deslab-bridge-src\bridge" (Cyrillic folder name) on the desktop (where the rebuild .bat files runs it);
// DESLAB_BRIDGE_DIR overrides that.
GS::UniString DeslabObmerPalette::BridgeFolder ()
{
#if defined (WINDOWS)
    const GS::UniString own = EnvironmentValue (L"DESLAB_BRIDGE_DIR");
    if (!own.IsEmpty ()) {
        return own;
    }
    const GS::UniString tail = U ("\\\xD0\x90\xD1\x80\xD1\x85\xD0\xB8\xD0\xBA\xD0\xB0\xD0\xB4\\deslab-bridge-src\\bridge");
    GS::Array<GS::UniString> desktops;
    PWSTR desktop = nullptr;
    if (SUCCEEDED (SHGetKnownFolderPath (FOLDERID_Desktop, 0, nullptr, &desktop)) && desktop != nullptr) {
        desktops.Push (FromWide (desktop));
    }
    if (desktop != nullptr) {
        CoTaskMemFree (desktop);
    }
    desktops.Push (EnvironmentValue (L"USERPROFILE") + "\\Desktop");
    for (const GS::UniString& folder : desktops) {
        if (FileExists (PythonIn (folder + tail))) {
            return folder + tail;
        }
    }
    return desktops.GetLast () + tail;
#else
    return GS::UniString ();
#endif
}

// The name rebuild.py gives a build: the scan file's name up to its first dot, or the preset itself.
GS::UniString DeslabObmerPalette::BuildName (const GS::UniString& fileOrPreset)
{
    std::string name (fileOrPreset.ToCStr (CC_UTF8).Get ());
    const size_t slash = name.find_last_of ("\\/");
    if (slash != std::string::npos) {
        name = name.substr (slash + 1);
    }
    const size_t dot = name.find ('.');
    if (dot != std::string::npos && dot > 0) {
        name = name.substr (0, dot);
    }
    return GS::UniString (name.c_str (), CC_UTF8);
}

bool DeslabObmerPalette::ChooseFile (const char* typeName, const char* extension, DG::TextEdit& target)
{
    FTM::FileTypeManager ftMan (typeName);
    FTM::GroupID gid = ftMan.AddGroup (typeName);
    ftMan.AddType (FTM::FileType (typeName, extension, '    ', '    ', 0), gid);
    DG::FileDialog dialog (DG::FileDialog::OpenFile);
    dialog.AddFilter (gid);
    if (!dialog.Invoke ()) {
        return false;
    }
    GS::UniString path;
    dialog.GetSelectedFile ().ToPath (&path);
    target.SetText (path);
    return true;
}

void DeslabObmerPalette::Build ()
{
    GS::UniString file = fileEdit.GetText ();
    file.Trim ();
    if (file.IsEmpty ()) {
        SetStatus (U ("\xD0\x92\xD1\x8B\xD0\xB1\xD0\xB5\xD1\x80\xD0\xB8\xD1\x82\xD0\xB5 \xD1\x81\xD0\xBA\xD0\xB0\xD0\xBD E57 \xD0\xB8\xD0\xBB\xD0\xB8 \xD0\xB2\xD0\xBF\xD0\xB8\xD1\x88\xD0\xB8\xD1\x82\xD0\xB5 \xD0\xBF\xD1\x80\xD0\xB5\xD1\x81\xD0\xB5\xD1\x82"));
        return;
    }
    const bool isScan = file.ToLowerCase ().EndsWith (".e57");
    const ModelChoice& choice = modelChoices[modelPopUp.GetSelectedItem () - 1];
    const GS::UniString modelId = choice.id;

    GS::Array<GS::UniString> args = { "-m", "deslab_bridge.rebuild", file, "--no-pictures" };
    if (modelId == "-") {
        if (isScan) {
            args.Push ("--no-ai");
        }
    } else {
        if (!isScan) {
            args.Push ("--ai");
        }
        if (!modelId.IsEmpty ()) {
            args.Push ("--model");
            args.Push (modelId);
        }
        GS::UniString pdf = pdfEdit.GetText ();
        pdf.Trim ();
        if (!pdf.IsEmpty ()) {
            GS::UniString page = pageEdit.GetText ();
            page.Trim ();
            args.Push ("--pdf");
            args.Push (pdf + ":" + (page.IsEmpty () ? GS::UniString ("1") : page));
        }
    }
    lastBuildName = BuildName (file);
    logEdit.SetText (GS::UniString ());
    StartJob (Job::Build, args);
    if (IsRunning ()) {
        SetStatus (modelId == "-" ? U ("\xD0\xA1\xD1\x82\xD1\x80\xD0\xBE\xD1\x8E \xD0\xB1\xD0\xB5\xD0\xB7 \xD0\x98\xD0\x98\xE2\x80\xA6") : U ("\xD0\xA1\xD1\x82\xD1\x80\xD0\xBE\xD1\x8E, \xD0\x98\xD0\x98 \xD1\x80\xD0\xB0\xD0\xB7\xD0\xB1\xD0\xB8\xD1\x80\xD0\xB0\xD0\xB5\xD1\x82 (1\xE2\x80\x93" "3 \xD0\xBC\xD0\xB8\xD0\xBD)\xE2\x80\xA6"));
    }
}

void DeslabObmerPalette::SendChatMessage ()
{
    GS::UniString message = messageEdit.GetText ();
    message.Trim ();
    if (message.IsEmpty ()) {
        return;
    }
    if (lastBuildName.IsEmpty ()) {
        lastBuildName = BuildName (fileEdit.GetText ());
    }
    if (lastBuildName.IsEmpty ()) {
        SetStatus (U ("\xD0\xA1\xD0\xBD\xD0\xB0\xD1\x87\xD0\xB0\xD0\xBB\xD0\xB0 \xD0\xB2\xD1\x8B\xD0\xB1\xD0\xB5\xD1\x80\xD0\xB8\xD1\x82\xD0\xB5 \xD1\x81\xD0\xBA\xD0\xB0\xD0\xBD \xD0\xB8\xD0\xBB\xD0\xB8 \xD0\xBF\xD1\x80\xD0\xB5\xD1\x81\xD0\xB5\xD1\x82"));
        return;
    }
    GS::Array<GS::UniString> args = { "-m", "deslab_bridge.chat", lastBuildName, message };
    const GS::UniString modelId = modelChoices[modelPopUp.GetSelectedItem () - 1].id;
    if (!modelId.IsEmpty () && modelId != "-") {
        args.Push ("--model");
        args.Push (modelId);
    }
    AppendOutput (Job::Chat, U ("\xD0\x92\xD1\x8B: ") + message + "\n" + U ("\xD0\x98\xD0\x98: "));
    messageEdit.SetText (GS::UniString ());
    StartJob (Job::Chat, args);
    if (IsRunning ()) {
        SetStatus (U ("\xD0\x98\xD0\x98 \xD0\xBE\xD1\x82\xD0\xB2\xD0\xB5\xD1\x87\xD0\xB0\xD0\xB5\xD1\x82\xE2\x80\xA6"));
    }
}

void DeslabObmerPalette::StartJob (Job newJob, const GS::Array<GS::UniString>& args)
{
    if (IsRunning ()) {
        return;
    }
    const GS::UniString bridge = BridgeFolder ();
    const GS::UniString python = PythonIn (bridge);
    if (!FileExists (python)) {
        AppendOutput (newJob, U ("\xD0\x9D\xD0\xB5 \xD0\xBD\xD0\xB0\xD0\xB9\xD0\xB4\xD0\xB5\xD0\xBD Python \xD0\xBC\xD0\xBE\xD1\x81\xD1\x82\xD0\xB0: ") + python + "\n");
        SetStatus (U ("\xD0\x9C\xD0\xBE\xD1\x81\xD1\x82 \xD0\xBD\xD0\xB5 \xD0\xBD\xD0\xB0\xD0\xB9\xD0\xB4\xD0\xB5\xD0\xBD"));
        return;
    }

    class ReaderThread : public GS::Runnable
    {
        GS::Process& process;
        Job          target;
        std::string  pendingOut;
        std::string  pendingErr;

        class OutputTask : public GS::Runnable {
            Job           target;
            GS::UniString text;
        public:
            OutputTask (Job t, const GS::UniString& s) : target (t), text (s) {}
            virtual void Run () override
            {
                ForgetPostedTask (this);
                if (DeslabObmerPalette::HasInstance ()) {
                    DeslabObmerPalette::Instance ().AppendOutput (target, text);
                }
            }
        };
        class FinishedTask : public GS::Runnable {
            int exitCode;
        public:
            explicit FinishedTask (int c) : exitCode (c) {}
            virtual void Run () override
            {
                ForgetPostedTask (this);
                if (DeslabObmerPalette::HasInstance ()) {
                    DeslabObmerPalette::Instance ().JobFinished (exitCode);
                }
            }
        };

        void Read (GS::IBinaryChannel& channel, std::string& pending)
        {
            if (channel.GetAvailable () <= 0) {
                return;
            }
            const GS::USize size = static_cast<GS::USize> (channel.GetAvailable ());
            std::unique_ptr<char[]> buffer (new char[size]);
            GS::IBinaryChannelUtilities::ReadFully (channel, buffer.get (), size);
            pending.append (buffer.get (), size);
            const GS::UniString text = TakeUTF8 (pending);
            if (!text.IsEmpty ()) {
                Post (new OutputTask (target, text));
            }
        }

    public:
        ReaderThread (GS::Process& p, Job t) : process (p), target (t) {}

        virtual void Run () override
        {
            const GS::Timeout timeout = {0, 0, 0, 50 /*ms*/};
            while (!process.WaitFor (timeout)) {
                if (quitting) {
                    return;
                }
                Read (process.GetStandardOutputChannel (), pendingOut);
                Read (process.GetStandardErrorChannel (), pendingErr);
            }
            if (quitting) {
                return;
            }
            Read (process.GetStandardOutputChannel (), pendingOut);
            Read (process.GetStandardErrorChannel (), pendingErr);
            Post (new FinishedTask (process.GetExitCode ()));
        }
    };

    try {
#if defined (WINDOWS)
        // UTF-8, unbuffered output and the bridge package on the path, set in Archicad's environment only while the
        // process is started (the same trick as TapirPalette's PYTHONIOENCODING).
        const wchar_t* names[] = { L"PYTHONIOENCODING", L"PYTHONUNBUFFERED", L"PYTHONPATH" };
        const GS::UniString values[] = { "utf-8", "1", bridge };
        GS::UniString saved[3];
        bool hadValue[3] = {};
        for (int i = 0; i < 3; ++i) {
            saved[i] = EnvironmentValue (names[i]);
            hadValue[i] = !saved[i].IsEmpty ();
            SetEnvironmentVariableW (names[i], reinterpret_cast<const wchar_t*> (values[i].ToUStr ().Get ()));
        }
        const GS::OnExit restore ([&] () {
            for (int i = 0; i < 3; ++i) {
                SetEnvironmentVariableW (names[i], hadValue[i] ? reinterpret_cast<const wchar_t*> (saved[i].ToUStr ().Get ()) : nullptr);
            }
        });
#endif
        process = GS::Process::Create (python, args, GS::Process::CreateNoWindow, true, false, true);
    } catch (...) {
        process = {};
    }
    if (!process.IsValid ()) {
        AppendOutput (newJob, U ("\xD0\x9D\xD0\xB5 \xD1\x83\xD0\xB4\xD0\xB0\xD0\xBB\xD0\xBE\xD1\x81\xD1\x8C \xD0\xB7\xD0\xB0\xD0\xBF\xD1\x83\xD1\x81\xD1\x82\xD0\xB8\xD1\x82\xD1\x8C \xD0\xBC\xD0\xBE\xD1\x81\xD1\x82: ") + python + "\n");
        SetStatus (U ("\xD0\x9E\xD1\x88\xD0\xB8\xD0\xB1\xD0\xBA\xD0\xB0 \xD0\xB7\xD0\xB0\xD0\xBF\xD1\x83\xD1\x81\xD0\xBA\xD0\xB0"));
        return;
    }
    job = newJob;
    UpdateButtons ();
    executor.Execute (new ReaderThread (process, newJob));
}

void DeslabObmerPalette::ButtonClicked (const DG::ButtonClickEvent& ev)
{
    if (ev.GetSource () == &browseButton) {
        ChooseFile ("E57", "e57", fileEdit);
    } else if (ev.GetSource () == &pdfButton) {
        ChooseFile ("PDF", "pdf", pdfEdit);
    } else if (ev.GetSource () == &buildButton) {
        Build ();
    } else if (ev.GetSource () == &stopButton) {
        if (IsRunning ()) {
            try {
                process.Kill ();
            } catch (...) {
            }
            SetStatus (U ("\xD0\x9E\xD1\x81\xD1\x82\xD0\xB0\xD0\xBD\xD0\xBE\xD0\xB2\xD0\xBB\xD0\xB5\xD0\xBD\xD0\xBE"));
        }
    } else if (ev.GetSource () == &sendButton) {
        SendChatMessage ();
    }
}

void DeslabObmerPalette::PanelCloseRequested (const DG::PanelCloseRequestEvent&, bool* accepted)
{
    hiddenByForcedHideBegin = false;
    Hide ();
    *accepted = true;
}

void DeslabObmerPalette::PanelResized (const DG::PanelResizeEvent& ev)
{
    const short dx = ev.GetHorizontalChange ();
    const short dy = ev.GetVerticalChange ();
    BeginMoveResizeItems ();
    fileLabel.Resize (dx, 0);
    fileEdit.Resize (dx, 0);
    browseButton.Move (dx, 0);
    pdfEdit.Resize (dx, 0);
    pdfButton.Move (dx, 0);
    pageLabel.Move (dx, 0);
    pageEdit.Move (dx, 0);
    modelPopUp.Resize (dx, 0);
    statusText.Resize (dx, 0);
    separator.Resize (dx, 0);
    logLabel.Resize (dx, 0);
    logEdit.Resize (dx, dy);               // the log takes the extra height
    chatLabel.MoveAndResize (0, dy, dx, 0);
    chatEdit.MoveAndResize (0, dy, dx, 0);
    messageEdit.MoveAndResize (0, dy, dx, 0);
    sendButton.Move (dx, dy);
    EndMoveResizeItems ();
}

void DeslabObmerPalette::KeepPostedTask (const GS::RunnableTask& task)
{
    std::lock_guard<std::mutex> lock (postedTasksMutex);
    finishedTasks.clear ();
    postedTasks.push_back (task);
}

void DeslabObmerPalette::ForgetPostedTask (const GS::Runnable* task)
{
    std::lock_guard<std::mutex> lock (postedTasksMutex);
    for (auto it = postedTasks.begin (); it != postedTasks.end (); ++it) {
        if (it->operator-> () == task) {
            finishedTasks.push_back (*it);
            postedTasks.erase (it);
            return;
        }
    }
}

void DeslabObmerPalette::Post (const GS::RunnableTask& task)
{
    if (!quitting) {
        KeepPostedTask (task);
        GS::MessageLoopExecutor ().Execute (task);
    }
}

void DeslabObmerPalette::StopOnQuit ()
{
    if (!HasInstance ()) {
        return;
    }
    quitting = true;
    DeslabObmerPalette& palette = Instance ();
    if (palette.IsRunning ()) {
        try {
            palette.process.Kill ();
        } catch (...) {
        }
    }
    palette.executor.Wait (GS::Timeout (0, 0, 2));
    std::lock_guard<std::mutex> lock (postedTasksMutex);
    finishedTasks.clear ();
    if (!postedTasks.empty ()) {
        new std::vector<GS::RunnableTask> (std::move (postedTasks));   // never freed on purpose, see TapirPalette
        postedTasks.clear ();
    }
}

GSErrCode DeslabObmerPalette::PaletteControlCallBack (Int32, API_PaletteMessageID messageID, GS::IntPtr param)
{
    switch (messageID) {
        case APIPalMsg_OpenPalette:
            Instance ().Show ();
            break;
        case APIPalMsg_ClosePalette:
            if (HasInstance ()) {
                Instance ().Hide ();
            }
            break;
        case APIPalMsg_HidePalette_Begin:
            if (HasInstance ()) {
                DeslabObmerPalette& palette = Instance ();
                palette.hiddenByForcedHideBegin = palette.IsVisible ();
                if (palette.hiddenByForcedHideBegin) {
                    palette.Hide ();
                }
            }
            break;
        case APIPalMsg_HidePalette_End:
            if (HasInstance ()) {
                DeslabObmerPalette& palette = Instance ();
                if (palette.hiddenByForcedHideBegin) {
                    palette.Show ();
                }
                palette.hiddenByForcedHideBegin = false;
            }
            break;
        case APIPalMsg_IsPaletteVisible:
            *(reinterpret_cast<bool*> (param)) = HasInstance () && Instance ().IsVisible ();
            break;
        default:
            break;
    }
    return NoError;
}

GSErrCode DeslabObmerPalette::RegisterPaletteControlCallBack ()
{
    return ACAPI_RegisterModelessWindow (
                    GS::CalculateHashValue (paletteGuid),
                    PaletteControlCallBack,
                    API_PalEnabled_FloorPlan + API_PalEnabled_Section + API_PalEnabled_Elevation +
                    API_PalEnabled_InteriorElevation + API_PalEnabled_3D + API_PalEnabled_Detail +
                    API_PalEnabled_Worksheet + API_PalEnabled_Layout + API_PalEnabled_DocumentFrom3D,
                    GSGuid2APIGuid (paletteGuid));
}
