#pragma once

#include "CommandBase.hpp"

// deslab: builds a BIM model (walls, doors, windows, floor slabs, zones) from a point cloud.
// The cloud is analysed by the deslab-scan2bim helper (scan2bim/ in the repository), which
// writes a scene JSON; this command reads the scene and creates the elements with the Tapir
// creation commands, so favorites, materials and layers work as in those commands.
class BuildModelFromScanCommand : public CommandBase
{
public:
    BuildModelFromScanCommand ();
    virtual GS::String GetName () const override;
    virtual GS::Optional<GS::UniString> GetInputParametersSchema () const override;
    virtual GS::Optional<GS::UniString> GetRawResponseSchema () const override;
    virtual GS::ObjectState Execute (const GS::ObjectState& parameters, GS::ProcessControl& processControl) const override;
};
