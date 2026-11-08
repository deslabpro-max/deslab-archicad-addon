#pragma once

#include "CommandBase.hpp"

// deslab: "Oformit proekt" (Igor 07.11.2026: the add-on is sold, so it must set up its office standard in any open
// project by itself, without copying a template file). Step 1: the attributes. They are read from the standard's source
// file (a .pln or .tpl, without opening it) and the missing ones are created in the open project, with their references
// to other attributes (fills, surfaces, building materials, line types, layers) moved to the new indices. Attributes
// that already exist under the same name are kept as they are.
class ApplyOfficeStandardCommand : public CommandBase
{
public:
    ApplyOfficeStandardCommand ();
    virtual GS::String GetName () const override;
    virtual GS::Optional<GS::UniString> GetInputParametersSchema () const override;
    virtual GS::Optional<GS::UniString> GetRawResponseSchema () const override;
    virtual GS::ObjectState Execute (const GS::ObjectState& parameters, GS::ProcessControl& processControl) const override;

    // The same work for the palette: returns one report line per attribute type.
    // packagePath may be empty: then layers, surfaces and building materials are skipped.
    static GSErrCode ApplyAttributes (const GS::UniString& sourceProjectPath, const GS::UniString& packagePath,
                                      GS::ProcessControl& processControl, GS::Array<GS::UniString>& reportLines);
};

// deslab: "Snyat standart". Archicad 27 does not read layers, surfaces and building materials from a file, so they are
// written from the open project (the standard's project or a copy of it) to a JSON package that ApplyOfficeStandard reads.
class CaptureOfficeStandardCommand : public CommandBase
{
public:
    CaptureOfficeStandardCommand ();
    virtual GS::String GetName () const override;
    virtual GS::Optional<GS::UniString> GetInputParametersSchema () const override;
    virtual GS::Optional<GS::UniString> GetRawResponseSchema () const override;
    virtual GS::ObjectState Execute (const GS::ObjectState& parameters, GS::ProcessControl& processControl) const override;

    static GSErrCode Capture (const GS::UniString& packagePath, GS::ProcessControl& processControl, GS::ObjectState& counts);
};
