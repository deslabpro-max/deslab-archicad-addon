#pragma once

// deslab: the "deslab Obmer" palette (Igor 06.11.2026: the add-on gets its own interface inside Archicad, drawn with
// Archicad's own DG controls). It only drives the Python bridge on this PC (bridge/deslab_bridge): "Build" (Postroit) runs
// `python -m deslab_bridge.rebuild <scan.e57 | preset> [--pdf file:page] [--model id | --no-ai]`, the chat runs
// `python -m deslab_bridge.chat <name> <message>`. The output is shown in the palette. The bridge does the scan
// analysis, the AI review and the building through the JSON API, as before.

#include "APIEnvir.h"
#include "ACAPinc.h"
#include "DGModule.hpp"
#include "ThreadedExecutor.hpp"
#include "Process.hpp"

#include <atomic>
#include <mutex>
#include <string>
#include <vector>

class DeslabObmerPalette final : public DG::Palette,
    public DG::PanelObserver,
    public DG::ButtonItemObserver
{
public:
    virtual ~DeslabObmerPalette ();

    static bool                HasInstance ();
    static DeslabObmerPalette& Instance ();

    void Show ();
    void Hide ();

    static GSErrCode RegisterPaletteControlCallBack ();

    // Called from FreeData: stops a running bridge process and its output reader before the add-on is unloaded.
    static void StopOnQuit ();

private:
    enum class Job { None, Build, Chat };

    DG::LeftText   fileLabel;
    DG::TextEdit   fileEdit;
    DG::Button     browseButton;
    DG::LeftText   pdfLabel;
    DG::TextEdit   pdfEdit;
    DG::Button     pdfButton;
    DG::LeftText   pageLabel;
    DG::TextEdit   pageEdit;
    DG::LeftText   modelLabel;
    DG::PopUp      modelPopUp;
    DG::Button     buildButton;
    DG::Button     stopButton;
    DG::LeftText   statusText;
    DG::Separator  separator;
    DG::LeftText   logLabel;
    DG::LeftText   chatLabel;
    DG::TextEdit   messageEdit;
    DG::Button     sendButton;
    DG::MultiLineEdit logEdit;
    DG::MultiLineEdit chatEdit;

    GS::Process          process;
    GS::ThreadedExecutor executor;
    Job                  job = Job::None;
    GS::UniString        lastBuildName;   // the build the chat is about

    void   InitTexts ();
    bool   IsRunning ();
    void   StartJob (Job newJob, const GS::Array<GS::UniString>& args);
    void   AppendOutput (Job target, const GS::UniString& text);
    void   JobFinished (int exitCode);
    void   SetStatus (const GS::UniString& text);
    void   UpdateButtons ();

    void   Build ();
    void   SendChatMessage ();
    bool   ChooseFile (const char* typeName, const char* extension, DG::TextEdit& target);

    static GS::UniString BridgeFolder ();
    static GS::UniString BuildName (const GS::UniString& fileOrPreset);

    virtual void PanelCloseRequested (const DG::PanelCloseRequestEvent& ev, bool* accepted) override;
    virtual void PanelResized (const DG::PanelResizeEvent& ev) override;
    virtual void ButtonClicked (const DG::ButtonClickEvent& ev) override;

    static GSErrCode PaletteControlCallBack (Int32 paletteId, API_PaletteMessageID messageID, GS::IntPtr param);

    // Tasks posted from the output reader thread to the main thread; kept alive here so none is destroyed after the
    // add-on is unloaded (the same scheme as TapirPalette).
    static void KeepPostedTask (const GS::RunnableTask& task);
    static void ForgetPostedTask (const GS::Runnable* task);
    static void Post (const GS::RunnableTask& task);

    static std::atomic<bool>              quitting;
    static std::mutex                     postedTasksMutex;
    static std::vector<GS::RunnableTask>  postedTasks;
    static std::vector<GS::RunnableTask>  finishedTasks;

    bool hiddenByForcedHideBegin = false;

    static const GS::Guid                paletteGuid;
    static GS::Ref<DeslabObmerPalette>   instance;

    DeslabObmerPalette ();
};
