// deslab: see OfficeStandardCommands.hpp and README.md.

#include "OfficeStandardCommands.hpp"
#include "MigrationHelper.hpp"
#include "FileSystem.hpp"
#include "File.hpp"
#include "ObjectStateJSONConversion.hpp"

#include <deque>
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

// Moves the references of a loaded attribute to the indices of the open project. Returns false when the attribute
// cannot be made right in this project (a layer combination whose layers did not come over), so it is not created.
bool RemapReferences (API_Attribute& attribute, API_AttributeDefExt& defs, std::map<API_AttrTypeID, IndexMap>& maps)
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
                // deslab: every layer of the combination must be in the project, otherwise the view would show the
                // wrong layers. Layers of the project that the standard does not know are hidden in it.
                const IndexMap& layers = maps[API_LayerID];
                auto* remapped = new GS::HashTable<API_AttributeIndex, API_LayerStat> ();
                bool complete = true;
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
                    } else {
                        complete = false;
                    }
                }
                GS::Array<API_Attribute> projectLayers;
                ACAPI_Attribute_GetAttributesByType (API_LayerID, projectLayers);
                for (API_Attribute& layer : projectLayers) {
                    if (!remapped->ContainsKey (layer.header.index)) {
                        API_LayerStat hidden = {};
                        hidden.lFlags = APILay_Hidden;
                        hidden.conClassId = layer.layer.conClassId;
                        remapped->Put (layer.header.index, hidden);
                    }
                    DisposeAttribute (layer);
                }
                delete defs.layer_statItems;
                defs.layer_statItems = remapped;
                attribute.layerComb.lNumb = static_cast<Int32> (remapped->GetSize ());
                if (!complete) {
                    return false;
                }
            }
            break;
        default:
            break;
    }
    return true;
}

// deslab: ACAPI_Attribute_GetAttributesFromProject returns no layers, surfaces or building materials in Archicad 27
// (tested on three files, 07.11.2026). These types come from the standard package instead: a JSON file captured from
// a project that holds the standard (CaptureOfficeStandard). Indices in it are the ones of that project, so the package
// and the standard file must be the same project (or copies of it).
bool FromPackage (API_AttrTypeID typeID)
{
    return typeID == API_LayerID || typeID == API_MaterialID || typeID == API_BuildingMaterialID || typeID == API_LayerCombID;
}

const char* PackageKey (API_AttrTypeID typeID)
{
    switch (typeID) {
        case API_LayerID:            return "layers";
        case API_MaterialID:         return "surfaces";
        case API_BuildingMaterialID: return "buildingMaterials";
        case API_LayerCombID:        return "layerCombinations";
        default:                     return "";
    }
}

GS::ObjectState ColorOS (const API_RGBColor& c)
{
    return GS::ObjectState ("red", c.f_red, "green", c.f_green, "blue", c.f_blue);
}

void ReadColor (const GS::ObjectState& os, const char* key, API_RGBColor& c)
{
    GS::ObjectState color;
    if (os.Get (key, color)) {
        color.Get ("red", c.f_red);
        color.Get ("green", c.f_green);
        color.Get ("blue", c.f_blue);
    }
}

template <typename T>
void ReadNumber (const GS::ObjectState& os, const char* key, T& target)
{
    double value;
    if (os.Get (key, value)) {
        target = static_cast<T> (value);
    }
}

GS::ObjectState CaptureAttribute (API_AttrTypeID typeID, API_AttributeIndex index)
{
    GS::ObjectState os;
    GS::UniString name, id, manufacturer, description;
    API_Attribute a = {};
    a.header.typeID = typeID;
    a.header.index = index;
    a.header.uniStringNamePtr = &name;
    if (typeID == API_BuildingMaterialID) {
        a.buildingMaterial.id = &id;
        a.buildingMaterial.manufacturer = &manufacturer;
        a.buildingMaterial.description = &description;
    }
    if (ACAPI_Attribute_Get (&a) != NoError) {
        DisposeAttribute (a);
        return os;
    }
    os.Add ("index", GetAttributeIndex (a.header.index));
    os.Add ("guid", APIGuidToString (a.header.guid));
    os.Add ("name", name);
    os.Add ("flags", static_cast<Int32> (a.header.flags));
    switch (typeID) {
        case API_LayerID:
            os.Add ("conClassId", a.layer.conClassId);
            break;
        case API_LayerCombID: {
            // deslab: the layer statuses come only from the open project; the file read gives none.
            const auto& stats = os.AddList<GS::ObjectState> ("layers");
            API_AttributeDefExt defs = {};
            if (ACAPI_Attribute_GetDefExt (API_LayerCombID, a.header.index, &defs) == NoError && defs.layer_statItems != nullptr) {
                for (const auto& kv : *defs.layer_statItems) {
#ifdef ServerMainVers_2800
                    const API_AttributeIndex& layerIndex = kv.key;
                    const API_LayerStat& stat = kv.value;
#else
                    const API_AttributeIndex& layerIndex = *kv.key;
                    const API_LayerStat& stat = *kv.value;
#endif
                    stats (GS::ObjectState ("layer", GetAttributeIndex (layerIndex),
                        "flags", static_cast<Int32> (stat.lFlags), "conClassId", stat.conClassId));
                }
            }
            ACAPI_DisposeAttrDefsHdlsExt (&defs);
            break;
        }
        case API_MaterialID: {
            const API_MaterialType& m = a.material;
            os.Add ("mtype", static_cast<Int32> (m.mtype));
            os.Add ("ambientPc", static_cast<Int32> (m.ambientPc));
            os.Add ("diffusePc", static_cast<Int32> (m.diffusePc));
            os.Add ("specularPc", static_cast<Int32> (m.specularPc));
            os.Add ("transpPc", static_cast<Int32> (m.transpPc));
            os.Add ("shine", static_cast<Int32> (m.shine));
            os.Add ("transpAtt", static_cast<Int32> (m.transpAtt));
            os.Add ("emissionAtt", static_cast<Int32> (m.emissionAtt));
            os.Add ("surfaceRGB", ColorOS (m.surfaceRGB));
            os.Add ("specularRGB", ColorOS (m.specularRGB));
            os.Add ("emissionRGB", ColorOS (m.emissionRGB));
            os.Add ("ifill", GetAttributeIndex (m.ifill));
            os.Add ("fillCol", static_cast<Int32> (m.fillCol));
            GS::ObjectState texture;
            texture.Add ("name", GS::UniString (m.texture.texName));
            texture.Add ("status", static_cast<Int32> (m.texture.status));
            texture.Add ("xSize", m.texture.xSize);
            texture.Add ("ySize", m.texture.ySize);
            texture.Add ("rotAng", m.texture.rotAng);
            os.Add ("texture", texture);
            break;
        }
        case API_BuildingMaterialID: {
            const API_BuildingMaterialType& b = a.buildingMaterial;
            os.Add ("id", id);
            os.Add ("manufacturer", manufacturer);
            os.Add ("description", description);
            os.Add ("connPriority", b.connPriority);
            os.Add ("cutFill", GetAttributeIndex (b.cutFill));
            os.Add ("cutFillPen", static_cast<Int32> (b.cutFillPen));
            os.Add ("cutFillBackgroundPen", static_cast<Int32> (b.cutFillBackgroundPen));
            os.Add ("cutMaterial", GetAttributeIndex (b.cutMaterial));
            os.Add ("cutFillOrientation", static_cast<Int32> (b.cutFillOrientation));
            os.Add ("thermalConductivity", b.thermalConductivity);
            os.Add ("density", b.density);
            os.Add ("heatCapacity", b.heatCapacity);
            os.Add ("embodiedEnergy", b.embodiedEnergy);
            os.Add ("embodiedCarbon", b.embodiedCarbon);
            os.Add ("showUncutLines", b.showUncutLines);
            os.Add ("doNotParticipateInCollDet", b.doNotParticipateInCollDet);
            break;
        }
        default:
            break;
    }
    DisposeAttribute (a);
    return os;
}

// Turns the package entries of one type into the same shape ACAPI_Attribute_GetAttributesFromProject returns.
// The building material texts point into strings, which must outlive the creation.
void LoadFromPackage (const GS::ObjectState& package, API_AttrTypeID typeID,
                      GS::Array<GS::Pair<API_Attribute, API_AttributeDefExt>>& loaded, GS::Array<GS::UniString>& names,
                      std::deque<GS::UniString>& strings)
{
    GS::Array<GS::ObjectState> entries;
    package.Get (PackageKey (typeID), entries);
    for (const GS::ObjectState& os : entries) {
        API_Attribute a = {};
        API_AttributeDefExt defs = {};
        a.header.typeID = typeID;
        Int32 index = 0;
        os.Get ("index", index);
        a.header.index = ACAPI_CreateAttributeIndex (index);
        GS::UniString guid;
        if (os.Get ("guid", guid)) {
            a.header.guid = APIGuidFromString (guid.ToCStr ());
        }
        GS::UniString name;
        os.Get ("name", name);
        names.Push (name);
        ReadNumber (os, "flags", a.header.flags);
        switch (typeID) {
            case API_LayerID:
                ReadNumber (os, "conClassId", a.layer.conClassId);
                break;
            case API_LayerCombID: {
                GS::Array<GS::ObjectState> stats;
                os.Get ("layers", stats);
                defs.layer_statItems = new GS::HashTable<API_AttributeIndex, API_LayerStat> ();
                for (const GS::ObjectState& statOS : stats) {
                    Int32 layer = 0;
                    statOS.Get ("layer", layer);
                    API_LayerStat stat = {};
                    ReadNumber (statOS, "flags", stat.lFlags);
                    ReadNumber (statOS, "conClassId", stat.conClassId);
                    defs.layer_statItems->Put (ACAPI_CreateAttributeIndex (layer), stat);
                }
                a.layerComb.lNumb = static_cast<Int32> (defs.layer_statItems->GetSize ());
                break;
            }
            case API_MaterialID: {
                API_MaterialType& m = a.material;
                Int32 mtype = 0;
                os.Get ("mtype", mtype);
                m.mtype = static_cast<API_MaterTypeID> (mtype);
                ReadNumber (os, "ambientPc", m.ambientPc);
                ReadNumber (os, "diffusePc", m.diffusePc);
                ReadNumber (os, "specularPc", m.specularPc);
                ReadNumber (os, "transpPc", m.transpPc);
                ReadNumber (os, "shine", m.shine);
                ReadNumber (os, "transpAtt", m.transpAtt);
                ReadNumber (os, "emissionAtt", m.emissionAtt);
                ReadColor (os, "surfaceRGB", m.surfaceRGB);
                ReadColor (os, "specularRGB", m.specularRGB);
                ReadColor (os, "emissionRGB", m.emissionRGB);
                Int32 ifill = 0;
                os.Get ("ifill", ifill);
                m.ifill = ACAPI_CreateAttributeIndex (ifill);
                ReadNumber (os, "fillCol", m.fillCol);
                GS::ObjectState texture;
                if (os.Get ("texture", texture)) {
                    SetUCharProperty (&texture, "name", m.texture.texName);
                    ReadNumber (texture, "status", m.texture.status);
                    texture.Get ("xSize", m.texture.xSize);
                    texture.Get ("ySize", m.texture.ySize);
                    texture.Get ("rotAng", m.texture.rotAng);
                }
                break;
            }
            case API_BuildingMaterialID: {
                API_BuildingMaterialType& b = a.buildingMaterial;
                strings.emplace_back ();
                os.Get ("id", strings.back ());
                b.id = &strings.back ();
                strings.emplace_back ();
                os.Get ("manufacturer", strings.back ());
                b.manufacturer = &strings.back ();
                strings.emplace_back ();
                os.Get ("description", strings.back ());
                b.description = &strings.back ();
                ReadNumber (os, "connPriority", b.connPriority);
                Int32 cutFill = 0, cutMaterial = 0, orientation = 0;
                os.Get ("cutFill", cutFill);
                os.Get ("cutMaterial", cutMaterial);
                os.Get ("cutFillOrientation", orientation);
                b.cutFill = ACAPI_CreateAttributeIndex (cutFill);
                b.cutMaterial = ACAPI_CreateAttributeIndex (cutMaterial);
                b.cutFillOrientation = static_cast<API_FillOrientationID> (orientation);
                ReadNumber (os, "cutFillPen", b.cutFillPen);
                ReadNumber (os, "cutFillBackgroundPen", b.cutFillBackgroundPen);
                os.Get ("thermalConductivity", b.thermalConductivity);
                os.Get ("density", b.density);
                os.Get ("heatCapacity", b.heatCapacity);
                os.Get ("embodiedEnergy", b.embodiedEnergy);
                os.Get ("embodiedCarbon", b.embodiedCarbon);
                os.Get ("showUncutLines", b.showUncutLines);
                os.Get ("doNotParticipateInCollDet", b.doNotParticipateInCollDet);
                break;
            }
            default:
                break;
        }
        loaded.Push (GS::Pair<API_Attribute, API_AttributeDefExt> (a, defs));
    }
}

struct TypeResult { Int32 created = 0; Int32 existing = 0; Int32 failed = 0; GS::Array<GS::UniString> failures; GSErrCode readError = NoError; };

TypeResult ApplyType (const IO::Location& source, const GS::ObjectState* package, API_AttrTypeID typeID,
                      std::map<API_AttrTypeID, IndexMap>& maps)
{
    TypeResult result;
    GS::Array<GS::Pair<API_Attribute, API_AttributeDefExt>> loaded;
    std::deque<GS::UniString> strings;
    GS::Array<GS::UniString> packageNames;
    if (FromPackage (typeID) && package != nullptr && package->Contains (PackageKey (typeID))) {
        LoadFromPackage (*package, typeID, loaded, packageNames, strings);
    } else {
        result.readError = ACAPI_Attribute_GetAttributesFromProject (source, typeID, &loaded);
    }
    std::map<GS::UniString, API_AttributeIndex> names = ProjectNames (typeID);
    IndexMap& map = maps[typeID];

    for (UIndex i = 0; i < loaded.GetSize (); ++i) {
        GS::Pair<API_Attribute, API_AttributeDefExt>& item = loaded[i];
        API_Attribute& attribute = item.first;
        API_AttributeDefExt& defs = item.second;
        const Int32 sourceIndex = GetAttributeIndex (attribute.header.index);
        GS::UniString name = LoadedName (attribute.header);
        if (i < packageNames.GetSize ()) {
            name = packageNames[i];   // the legacy 8-bit name cannot hold every character
        }

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
            if (!RemapReferences (attribute, defs, maps)) {
                ++result.failed;
                if (result.failures.GetSize () < 5) {
                    result.failures.Push (name + " (layers missing)");
                }
                attribute.header.uniStringNamePtr = nullptr;
                ACAPI_DisposeAttrDefsHdlsExt (&defs);
                DisposeAttribute (attribute);
                continue;
            }
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

bool ReadPackage (const GS::UniString& path, GS::ObjectState& package)
{
    const IO::Location location (path);
    IO::File file (location);
    if (file.Open (IO::File::OpenMode::ReadMode) != NoError) {
        return false;
    }
    const GSErrCode err = JSON::ConvertToObjectState (file, package);
    file.Close ();
    return err == NoError && !package.IsEmpty ();
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
            },
            "packagePath": {
                "type": "string",
                "description": "The standard package (.json) written by CaptureOfficeStandard from the same project. Layers, surfaces and building materials come from it, because Archicad does not read them from a file.",
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

GSErrCode ApplyOfficeStandardCommand::ApplyAttributes (const GS::UniString& sourceProjectPath, const GS::UniString& packagePath,
                                                       GS::Array<GS::UniString>& reportLines)
{
#ifdef ServerMainVers_2700
    const IO::Location source (sourceProjectPath);
    bool exists = false;
    if (IO::fileSystem.Contains (source, &exists) != NoError || !exists) {
        reportLines.Push ("The standard file does not exist: " + sourceProjectPath);
        return APIERR_BADPARS;
    }
    GS::ObjectState package;
    const bool hasPackage = !packagePath.IsEmpty ();
    if (hasPackage && !ReadPackage (packagePath, package)) {
        reportLines.Push ("The standard package cannot be read: " + packagePath);
        return APIERR_BADPARS;
    }
    if (!hasPackage) {
        reportLines.Push ("No standard package: layers, surfaces and building materials are skipped.");
    }
    return ACAPI_CallUndoableCommand ("Apply office standard", [&] () -> GSErrCode {
        std::map<API_AttrTypeID, IndexMap> maps;
        for (const StandardType& type : standardTypes) {
            const TypeResult result = ApplyType (source, hasPackage ? &package : nullptr, type.typeID, maps);
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
    UNUSED_PARAMETER (packagePath);
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
    GS::UniString packagePath;
    parameters.Get ("packagePath", packagePath);
    GS::Array<GS::UniString> reportLines;
    const GSErrCode err = ApplyAttributes (sourceProjectPath, packagePath, reportLines);
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

CaptureOfficeStandardCommand::CaptureOfficeStandardCommand () :
    CommandBase (CommonSchema::NotUsed)
{
}

GS::String CaptureOfficeStandardCommand::GetName () const
{
    return "CaptureOfficeStandard";
}

GS::Optional<GS::UniString> CaptureOfficeStandardCommand::GetInputParametersSchema () const
{
    return R"({
        "type": "object",
        "properties": {
            "packagePath": {
                "type": "string",
                "description": "The .json file to write the standard package to. It is overwritten.",
                "minLength": 1
            }
        },
        "additionalProperties": false,
        "required": ["packagePath"]
    })";
}

GS::Optional<GS::UniString> CaptureOfficeStandardCommand::GetRawResponseSchema () const
{
    return R"({
        "type": "object",
        "properties": {
            "layers": { "type": "integer" },
            "surfaces": { "type": "integer" },
            "buildingMaterials": { "type": "integer" },
            "layerCombinations": { "type": "integer" }
        },
        "additionalProperties": false,
        "required": ["layers", "surfaces", "buildingMaterials", "layerCombinations"]
    })";
}

GSErrCode CaptureOfficeStandardCommand::Capture (const GS::UniString& packagePath, GS::ObjectState& counts)
{
#ifdef ServerMainVers_2700
    GS::ObjectState package;
    package.Add ("format", GS::UniString ("deslab-office-standard"));
    package.Add ("version", 1);
    const API_AttrTypeID types[] = { API_LayerID, API_MaterialID, API_BuildingMaterialID, API_LayerCombID };
    for (API_AttrTypeID typeID : types) {
        const auto& list = package.AddList<GS::ObjectState> (PackageKey (typeID));
        GS::Array<API_Attribute> attributes;
        ACAPI_Attribute_GetAttributesByType (typeID, attributes);
        Int32 count = 0;
        for (API_Attribute& attribute : attributes) {
            const GS::ObjectState os = CaptureAttribute (typeID, attribute.header.index);
            if (!os.IsEmpty ()) {
                list (os);
                ++count;
            }
            DisposeAttribute (attribute);
        }
        counts.Add (PackageKey (typeID), count);
    }
    const IO::Location location (packagePath);
    IO::File file (location, IO::File::OnNotFound::Create);
    GSErrCode err = file.Open (IO::File::OpenMode::WriteEmptyMode);
    if (err == NoError) {
        err = JSON::CreateFromObjectState (package, file, true);
        file.Close ();
    }
    return err;
#else
    UNUSED_PARAMETER (packagePath);
    UNUSED_PARAMETER (counts);
    return APIERR_GENERAL;
#endif
}

GS::ObjectState CaptureOfficeStandardCommand::Execute (const GS::ObjectState& parameters, GS::ProcessControl& /*processControl*/) const
{
    GS::UniString packagePath;
    if (!parameters.Get ("packagePath", packagePath) || packagePath.IsEmpty ()) {
        return CreateErrorResponse (APIERR_BADPARS, "packagePath is missing.");
    }
    GS::ObjectState counts;
    const GSErrCode err = Capture (packagePath, counts);
    if (err != NoError) {
        return CreateErrorResponse (err, "Failed to write the standard package.");
    }
    return counts;
}
