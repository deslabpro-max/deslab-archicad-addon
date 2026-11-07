// deslab: see OfficeStandardCommands.hpp and README.md.

#include "OfficeStandardCommands.hpp"
#include "MigrationHelper.hpp"
#include "FileSystem.hpp"

#include <map>

namespace {

#ifdef ServerMainVers_2700

// The order matters: a type is created after every type it refers to.
struct StandardType { API_AttrTypeID typeID; const char* name; };
const StandardType standardTypes[] = {
    { API_PenTableID,         "PenTable" },
    { API_LinetypeID,         "Line" },
    { API_FilltypeID,         "Fill" },
    { API_MaterialID,         "Surface" },
    { API_BuildingMaterialID, "BuildingMaterial" },
    { API_CompWallID,         "Composite" },
    { API_LayerID,            "Layer" },
    { API_LayerCombID,        "LayerCombination" },
    { API_ZoneCatID,          "ZoneCategory" },
};

using IndexMap = std::map<Int32, API_AttributeIndex>;   // source index -> index in the open project

GS::UniString LoadedName (const API_Attr_Head& header)
{
    if (header.uniStringNamePtr != nullptr && !header.uniStringNamePtr->IsEmpty ()) {
        return *header.uniStringNamePtr;
    }
    return GS::UniString (header.name);
}

// The Unicode names of the open project's attributes of one type (the legacy 8-bit name garbles non-ASCII text).
std::map<GS::UniString, API_AttributeIndex> ProjectNames (API_AttrTypeID typeID)
{
    std::map<GS::UniString, API_AttributeIndex> out;
    GS::Array<API_Attribute> attributes;
    ACAPI_Attribute_GetAttributesByType (typeID, attributes);
    for (API_Attribute& attribute : attributes) {
        GS::UniString name;
        API_Attribute full = {};
        full.header.typeID = typeID;
        full.header.index = attribute.header.index;
        full.header.uniStringNamePtr = &name;
        if (ACAPI_Attribute_Get (&full) == NoError) {
            out[name] = attribute.header.index;
        }
        DisposeAttribute (full);
        DisposeAttribute (attribute);
    }
    return out;
}

bool FindByGuid (API_AttrTypeID typeID, const API_Guid& guid, API_AttributeIndex& index)
{
    if (guid == APINULLGuid) {
        return false;
    }
    API_Attribute attribute = {};
    attribute.header.typeID = typeID;
    attribute.header.guid = guid;
    const bool found = ACAPI_Attribute_Get (&attribute) == NoError;
    if (found) {
        index = attribute.header.index;
    }
    DisposeAttribute (attribute);
    return found;
}

void Remap (API_AttributeIndex& index, const IndexMap& map)
{
    const auto it = map.find (GetAttributeIndex (index));
    if (it != map.end ()) {
        index = it->second;
    }
}

// Moves the references of a loaded attribute to the indices of the open project.
void RemapReferences (API_Attribute& attribute, API_AttributeDefExt& defs, std::map<API_AttrTypeID, IndexMap>& maps)
{
    switch (attribute.header.typeID) {
        case API_MaterialID:
            Remap (attribute.material.ifill, maps[API_FilltypeID]);
            break;
        case API_BuildingMaterialID:
            Remap (attribute.buildingMaterial.cutFill, maps[API_FilltypeID]);
            Remap (attribute.buildingMaterial.cutMaterial, maps[API_MaterialID]);
            break;
        case API_CompWallID:
            if (defs.cwall_compItems != nullptr) {
                for (short i = 0; i < attribute.compWall.nComps; ++i) {
                    Remap ((*defs.cwall_compItems)[i].buildingMaterial, maps[API_BuildingMaterialID]);
                }
            }
            if (defs.cwall_compLItems != nullptr) {
                for (short i = 0; i <= attribute.compWall.nComps; ++i) {
                    Remap ((*defs.cwall_compLItems)[i].ltypeInd, maps[API_LinetypeID]);
                }
            }
            break;
        case API_LayerCombID:
            if (defs.layer_statItems != nullptr) {
                auto* remapped = new GS::HashTable<API_AttributeIndex, API_LayerStat> ();
                const IndexMap& layers = maps[API_LayerID];
                for (const auto& kv : *defs.layer_statItems) {
#ifdef ServerMainVers_2800
                    API_AttributeIndex layerIndex = kv.key;
                    const API_LayerStat& stat = kv.value;
#else
                    API_AttributeIndex layerIndex = *kv.key;
                    const API_LayerStat& stat = *kv.value;
#endif
                    const auto it = layers.find (GetAttributeIndex (layerIndex));
                    if (it != layers.end ()) {
                        remapped->Put (it->second, stat);
                    }
                }
                delete defs.layer_statItems;
                defs.layer_statItems = remapped;
            }
            break;
        default:
            break;
    }
}

struct TypeResult { Int32 created = 0; Int32 existing = 0; Int32 failed = 0; GS::Array<GS::UniString> failures; GSErrCode readError = NoError; };

TypeResult ApplyType (const IO::Location& source, API_AttrTypeID typeID, std::map<API_AttrTypeID, IndexMap>& maps)
{
    TypeResult result;
    GS::Array<GS::Pair<API_Attribute, API_AttributeDefExt>> loaded;
    result.readError = ACAPI_Attribute_GetAttributesFromProject (source, typeID, &loaded);
    std::map<GS::UniString, API_AttributeIndex> names = ProjectNames (typeID);
    IndexMap& map = maps[typeID];

    for (GS::Pair<API_Attribute, API_AttributeDefExt>& item : loaded) {
        API_Attribute& attribute = item.first;
        API_AttributeDefExt& defs = item.second;
        const Int32 sourceIndex = GetAttributeIndex (attribute.header.index);
        GS::UniString name = LoadedName (attribute.header);

        // An attribute that is already there (same guid, as in copies of the template, or same name) is kept as it is.
        API_AttributeIndex existingIndex = APIInvalidAttributeIndex;
        const auto byName = names.find (name);
        if (FindByGuid (typeID, attribute.header.guid, existingIndex)) {
            map[sourceIndex] = existingIndex;
            ++result.existing;
        } else if (byName != names.end ()) {
            map[sourceIndex] = byName->second;
            ++result.existing;
        } else {
            RemapReferences (attribute, defs, maps);
            attribute.header.index = APIInvalidAttributeIndex;
            attribute.header.uniStringNamePtr = &name;
            // The source guid is kept, so a second run finds the attribute again whatever its name looks like.
            const GSErrCode err = ACAPI_Attribute_CreateExt (&attribute, &defs);
            if (err == NoError) {
                map[sourceIndex] = attribute.header.index;
                names[name] = attribute.header.index;
                ++result.created;
            } else {
                ++result.failed;
                if (result.failures.GetSize () < 5) {
                    result.failures.Push (name + GS::UniString::Printf (" (%d)", static_cast<int> (err)));
                }
            }
        }
        attribute.header.uniStringNamePtr = nullptr;
        ACAPI_DisposeAttrDefsHdlsExt (&defs);
        DisposeAttribute (attribute);
    }
    return result;
}

#endif

}   // namespace

ApplyOfficeStandardCommand::ApplyOfficeStandardCommand () :
    CommandBase (CommonSchema::NotUsed)
{
}

GS::String ApplyOfficeStandardCommand::GetName () const
{
    return "ApplyOfficeStandard";
}

GS::Optional<GS::UniString> ApplyOfficeStandardCommand::GetInputParametersSchema () const
{
    return R"({
        "type": "object",
        "properties": {
            "sourceProjectPath": {
                "type": "string",
                "description": "The project or template file (.pln, .tpl) that holds the office standard. It is only read.",
                "minLength": 1
            }
        },
        "additionalProperties": false,
        "required": ["sourceProjectPath"]
    })";
}

GS::Optional<GS::UniString> ApplyOfficeStandardCommand::GetRawResponseSchema () const
{
    return R"({
        "type": "object",
        "properties": {
            "report": {
                "type": "array",
                "items": { "type": "string" }
            }
        },
        "additionalProperties": true,
        "required": ["report"]
    })";
}

GSErrCode ApplyOfficeStandardCommand::ApplyAttributes (const GS::UniString& sourceProjectPath, GS::Array<GS::UniString>& reportLines)
{
#ifdef ServerMainVers_2700
    const IO::Location source (sourceProjectPath);
    bool exists = false;
    if (IO::fileSystem.Contains (source, &exists) != NoError || !exists) {
        reportLines.Push ("The standard file does not exist: " + sourceProjectPath);
        return APIERR_BADPARS;
    }
    return ACAPI_CallUndoableCommand ("Apply office standard", [&] () -> GSErrCode {
        std::map<API_AttrTypeID, IndexMap> maps;
        for (const StandardType& type : standardTypes) {
            const TypeResult result = ApplyType (source, type.typeID, maps);
            GS::UniString line = GS::UniString::Printf ("%s: created %d, already there %d, failed %d",
                type.name, static_cast<int> (result.created), static_cast<int> (result.existing), static_cast<int> (result.failed));
            if (result.readError != NoError) {
                line += GS::UniString::Printf (", read error %d", static_cast<int> (result.readError));
            }
            for (const GS::UniString& failure : result.failures) {
                line += "; " + failure;
            }
            reportLines.Push (line);
        }
        return NoError;
    });
#else
    UNUSED_PARAMETER (sourceProjectPath);
    reportLines.Push ("ApplyOfficeStandard needs Archicad 27 or later.");
    return APIERR_GENERAL;
#endif
}

GS::ObjectState ApplyOfficeStandardCommand::Execute (const GS::ObjectState& parameters, GS::ProcessControl& /*processControl*/) const
{
    GS::UniString sourceProjectPath;
    if (!parameters.Get ("sourceProjectPath", sourceProjectPath) || sourceProjectPath.IsEmpty ()) {
        return CreateErrorResponse (APIERR_BADPARS, "sourceProjectPath is missing.");
    }
    GS::Array<GS::UniString> reportLines;
    const GSErrCode err = ApplyAttributes (sourceProjectPath, reportLines);
    if (err != NoError && reportLines.IsEmpty ()) {
        return CreateErrorResponse (err, "Failed to apply the office standard.");
    }
    GS::ObjectState response;
    const auto& report = response.AddList<GS::UniString> ("report");
    for (const GS::UniString& line : reportLines) {
        report (line);
    }
    if (err != NoError) {
        response.Add ("errorCode", static_cast<Int32> (err));
    }
    return response;
}
