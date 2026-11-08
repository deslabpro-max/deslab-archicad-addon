// deslab: see OfficeStandardCommands.hpp and README.md.

#include "OfficeStandardCommands.hpp"
#include "MigrationHelper.hpp"
#include "GraphicOverrideCommands.hpp"
#include "FileSystem.hpp"
#include "File.hpp"
#include "ObjectStateJSONConversion.hpp"

#include <cstdlib>
#include <deque>
#include <functional>
#include <string>
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
using GuidMap = std::map<GS::UniString, GS::UniString>;  // source guid -> guid in the open project, where they differ

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

void NoteGuid (GuidMap& guids, const API_Guid& sourceGuid, API_AttrTypeID typeID, API_AttributeIndex index)
{
    const API_Guid target = GetAttributeGuidFromIndex (typeID, index);
    if (sourceGuid != APINULLGuid && target != APINULLGuid && sourceGuid != target) {
        guids[APIGuidToString (sourceGuid).ToUpperCase ()] = APIGuidToString (target).ToUpperCase ();
    }
}

TypeResult ApplyType (const IO::Location& source, const GS::ObjectState* package, API_AttrTypeID typeID,
                      std::map<API_AttrTypeID, IndexMap>& maps, GuidMap& guids)
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

        // An attribute that is already there under the same name is kept as it is. deslab: the guid alone is not
        // enough: Archicad's own default surfaces share guids with differently named template surfaces, so a guid
        // held by another name gets the new attribute a fresh guid instead.
        API_AttributeIndex existingIndex = APIInvalidAttributeIndex;
        const auto byName = names.find (name);
        const API_Guid sourceGuid = attribute.header.guid;
        if (byName != names.end ()) {
            map[sourceIndex] = byName->second;
            NoteGuid (guids, sourceGuid, typeID, byName->second);
            ++result.existing;
        } else {
            if (FindByGuid (typeID, attribute.header.guid, existingIndex)) {
                attribute.header.guid = APINULLGuid;
            }
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
            const GSErrCode err = ACAPI_Attribute_CreateExt (&attribute, &defs);
            if (err == NoError) {
                map[sourceIndex] = attribute.header.index;
                names[name] = attribute.header.index;
                NoteGuid (guids, sourceGuid, typeID, attribute.header.index);
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

// deslab: graphic override rules and combinations. They refer to attributes (layers, fills, surfaces, building
// materials...) by guid, in the criterion XML and in the style, so the guids that changed on the way are replaced.
GS::UniString ReplaceGuids (GS::UniString text, const GuidMap& guids)
{
    for (const auto& kv : guids) {
        text.ReplaceAll (kv.first, kv.second);
        text.ReplaceAll (kv.first.ToLowerCase (), kv.second.ToLowerCase ());
    }
    return text;
}

// The rule as JSON text with the changed guids replaced (in the criterion XML and in the style alike).
GS::ObjectState WithGuidsReplaced (const GS::ObjectState& rule, const GuidMap& guids)
{
    GS::UniString json;
    GS::ObjectState out;
    if (JSON::CreateFromObjectState (rule, json) != NoError || JSON::ConvertToObjectState (ReplaceGuids (json, guids), out) != NoError) {
        return rule;
    }
    return out;
}

std::map<GS::UniString, API_Guid> RuleNames ()
{
    std::map<GS::UniString, API_Guid> out;
    GS::Array<API_Guid> ids;
    ACAPI_GraphicalOverride_GetOverrideRuleList (ids);
    for (const API_Guid& id : ids) {
        API_OverrideRule rule = {};
        rule.guid = id;
        if (ACAPI_GraphicalOverride_GetOverrideRuleById (rule) == NoError) {
            out[rule.name] = id;
        }
    }
    return out;
}

std::map<GS::UniString, API_Guid> CombinationNames ()
{
    std::map<GS::UniString, API_Guid> out;
    GS::Array<API_Guid> ids;
    ACAPI_GraphicalOverride_GetOverrideCombinationList (ids);
    for (const API_Guid& id : ids) {
        API_OverrideCombination combination = {};
        combination.guid = id;
        if (ACAPI_GraphicalOverride_GetOverrideCombination (combination, nullptr) == NoError) {
            out[combination.name] = id;
        }
    }
    return out;
}

GS::UniString FirstError (const GS::ObjectState& response, const char* listKey)
{
    GS::Array<GS::ObjectState> items;
    response.Get (listKey, items);
    for (const GS::ObjectState& item : items) {
        GS::ObjectState error;
        GS::UniString message;
        if (item.Get ("error", error) && error.Get ("message", message)) {
            return message;
        }
    }
    GS::ObjectState error;
    GS::UniString message;
    if (response.Get ("error", error) && error.Get ("message", message)) {
        return message;
    }
    return GS::EmptyUniString;
}

GS::UniString CreatedGuid (const GS::ObjectState& response, const char* listKey, const char* idKey)
{
    GS::Array<GS::ObjectState> items;
    response.Get (listKey, items);
    GS::ObjectState idOs;
    GS::UniString guid;
    if (items.GetSize () == 1 && items[0].Get (idKey, idOs) && idOs.Get ("guid", guid)) {
        return guid.ToUpperCase ();
    }
    return GS::EmptyUniString;
}

// deslab: a criterion on the Layer property keeps the layer's index ("Property::AttributeIndexValue"), not its guid:
// the indices of the standard's layers are moved to the ones in the open project.
GS::UniString RemapLayerIndices (const GS::UniString& xml, const IndexMap& layers)
{
    static const std::string layerProperty = "0F43553E-58E7-4661-8CEB-8B4E5D2ED50A";
    std::string text (xml.ToCStr (0, MaxUSize, CC_UTF8).Get ());
    size_t pos = 0;
    while ((pos = text.find ("<PropertyCriterion", pos)) != std::string::npos) {
        const size_t end = text.find ("</PropertyCriterion>", pos);
        if (end == std::string::npos) {
            break;
        }
        const size_t guid = text.find (layerProperty, pos);
        const size_t variant = text.find ("Property::AttributeIndexValue", pos);
        if (guid != std::string::npos && guid < end && variant != std::string::npos && variant < end) {
            const size_t open = text.find ("<Value>", variant);
            const size_t close = open == std::string::npos ? std::string::npos : text.find ("</Value>", open);
            if (close != std::string::npos && close < end) {
                const size_t from = open + 7;
                const Int32 index = static_cast<Int32> (std::atoi (text.substr (from, close - from).c_str ()));
                const auto it = layers.find (index);
                if (it != layers.end ()) {
                    const std::string mapped = std::to_string (GetAttributeIndex (it->second));
                    text.replace (from, close - from, mapped);
                }
            }
        }
        pos = text.find ("</PropertyCriterion>", pos) + 1;
    }
    return GS::UniString (text.c_str (), CC_UTF8);
}

void ApplyGraphicOverrides (const GS::ObjectState& package, const GuidMap& attributeGuids, const IndexMap& layers, GS::ProcessControl& processControl,
                            GS::Array<GS::UniString>& reportLines)
{
    GS::Array<GS::ObjectState> rules, combinations;
    if (!package.Get ("graphicOverrideRules", rules)) {
        return;
    }
    package.Get ("graphicOverrideCombinations", combinations);

    GuidMap ruleGuids;   // source rule guid -> rule guid in the open project
    std::map<GS::UniString, API_Guid> existingRules = RuleNames ();
    Int32 created = 0, existing = 0, failed = 0;
    GS::UniString failures;
    for (GS::ObjectState rule : rules) {
        GS::UniString name, sourceGuid;
        GS::ObjectState idOs;
        rule.Get ("name", name);
        if (rule.Get ("graphicOverrideRuleId", idOs)) {
            idOs.Get ("guid", sourceGuid);
            sourceGuid = sourceGuid.ToUpperCase ();
        }
        const auto it = existingRules.find (name);
        if (it != existingRules.end ()) {
            ruleGuids[sourceGuid] = APIGuidToString (it->second).ToUpperCase ();
            ++existing;
            continue;
        }
        rule = WithGuidsReplaced (rule, attributeGuids);
        {
            // Rebuilt with the fields CreateGraphicOverrideRules reads, the criterion's layer indices moved.
            GS::ObjectState rebuilt;
            GS::UniString text;
            if (rule.Get ("name", text)) {
                rebuilt.Add ("name", text);
            }
            if (rule.Get ("ruleGroupName", text)) {
                rebuilt.Add ("ruleGroupName", text);
            }
            if (rule.Get ("criterionXML", text)) {
                rebuilt.Add ("criterionXML", RemapLayerIndices (text, layers));
            }
            GS::ObjectState style;
            if (rule.Get ("style", style)) {
                rebuilt.Add ("style", style);
            }
            rule = rebuilt;
        }
        if (!rule.Contains ("ruleGroupName")) {
            rule.Add ("ruleGroupName", GS::UniString ("deslab"));
        }
        GS::ObjectState parameters;
        parameters.Add ("graphicOverrideRules", GS::Array<GS::ObjectState> { rule });
        const GS::ObjectState response = CreateGraphicOverrideRulesCommand ().Execute (parameters, processControl);
        const GS::UniString newGuid = CreatedGuid (response, "graphicOverrideRuleIds", "graphicOverrideRuleId");
        if (!newGuid.IsEmpty ()) {
            ruleGuids[sourceGuid] = newGuid;
            ++created;
        } else {
            ++failed;
            Int32 code = 0;
            GS::Array<GS::ObjectState> items;
            GS::ObjectState error;
            if (response.Get ("graphicOverrideRuleIds", items) && !items.IsEmpty () && items[0].Get ("error", error)) {
                error.Get ("code", code);
            }
            failures += "; " + name + GS::UniString::Printf (" (%d)", static_cast<int> (code));
        }
    }
    reportLines.Push (GS::UniString::Printf ("GraphicOverrideRule: created %d, already there %d, failed %d",
        static_cast<int> (created), static_cast<int> (existing), static_cast<int> (failed)) + failures);

    std::map<GS::UniString, API_Guid> existingCombinations = CombinationNames ();
    created = existing = failed = 0;
    failures.Clear ();
    for (const GS::ObjectState& combination : combinations) {
        GS::UniString name;
        combination.Get ("name", name);
        if (existingCombinations.find (name) != existingCombinations.end ()) {
            ++existing;
            continue;
        }
        GS::Array<GS::ObjectState> sourceIds;
        combination.Get ("graphicOverrideRuleIds", sourceIds);
        GS::Array<GS::ObjectState> ruleIds;
        Int32 missing = 0;
        for (const GS::ObjectState& item : sourceIds) {
            GS::ObjectState idOs;
            GS::UniString guid;
            if (item.Get ("graphicOverrideRuleId", idOs) && idOs.Get ("guid", guid)) {
                const auto it = ruleGuids.find (guid.ToUpperCase ());
                if (it != ruleGuids.end ()) {
                    ruleIds.Push (GS::ObjectState ("graphicOverrideRuleId", GS::ObjectState ("guid", it->second)));
                    continue;
                }
            }
            ++missing;
        }
        if (missing > 0) {
            // deslab: made with the rules that came over, so the views still find their combination.
            failures += GS::UniString::Printf ("; %T without %d rules", name.ToPrintf (), static_cast<int> (missing));
        }
        GS::ObjectState item;
        item.Add ("name", name);
        item.Add ("graphicOverrideRuleIds", ruleIds);
        GS::ObjectState parameters;
        parameters.Add ("graphicOverrideCombinations", GS::Array<GS::ObjectState> { item });
        const GS::ObjectState response = CreateGraphicOverrideCombinationsCommand ().Execute (parameters, processControl);
        if (!CreatedGuid (response, "graphicOverrideCombinationIds", "graphicOverrideCombinationId").IsEmpty ()) {
            ++created;
        } else {
            ++failed;
            if (failed <= 3) {
                failures += "; " + name + " (" + FirstError (response, "graphicOverrideCombinationIds") + ")";
            }
        }
    }
    reportLines.Push (GS::UniString::Printf ("GraphicOverrideCombination: created %d, already there %d, failed %d",
        static_cast<int> (created), static_cast<int> (existing), static_cast<int> (failed)) + failures);
}

// deslab: views of the View Map (story views only for now). Model view options and dimension standards cannot be
// created through the API, so each view keeps their names and, for a project without them, a copy of the settings as
// custom view settings (the raw Archicad 27 structures, hex encoded; the GDL part of the model view options is left out).
template <typename T>
GS::UniString ToHex (const T& value)
{
    static const char digits[] = "0123456789abcdef";
    const unsigned char* bytes = reinterpret_cast<const unsigned char*> (&value);
    std::string out;
    for (size_t i = 0; i < sizeof (T); ++i) {
        out += digits[bytes[i] >> 4];
        out += digits[bytes[i] & 0xF];
    }
    return GS::UniString (out.c_str ());
}

template <typename T>
bool FromHex (const GS::UniString& hex, T& value)
{
    const std::string text (hex.ToCStr ().Get ());
    if (text.size () != sizeof (T) * 2) {
        return false;
    }
    unsigned char* bytes = reinterpret_cast<unsigned char*> (&value);
    for (size_t i = 0; i < sizeof (T); ++i) {
        unsigned int byte = 0;
        for (size_t j = 0; j < 2; ++j) {
            const char c = text[i * 2 + j];
            byte = byte * 16 + ((c >= '0' && c <= '9') ? static_cast<unsigned int> (c - '0') : static_cast<unsigned int> (c - 'a' + 10));
        }
        bytes[i] = static_cast<unsigned char> (byte);
    }
    return true;
}

// deslab: looked up by index: the search by Unicode name does not find the template's sets.
bool FindModelViewOptions (const GS::UniString& name, API_ModelViewOptions* options)
{
    if (name.IsEmpty ()) {
        return false;
    }
    UInt32 count = 0;
    ACAPI_Navigator_ModelViewOptions_GetNum (count);
    for (UInt32 i = 1; i <= count; ++i) {
        API_ModelViewOptionsType mvo = {};
        GS::UniString mvoName;
        mvo.head.index = static_cast<Int32> (i);
        mvo.head.uniStringNamePtr = &mvoName;
        if (ACAPI_Navigator_ModelViewOptions_Get (&mvo) != NoError) {
            continue;
        }
        const bool match = mvoName == name || GS::UniString (mvo.head.name) == name;
        if (match && options != nullptr) {
            *options = mvo.modelViewOpt;
            options->gdlOptions = nullptr;
        }
        if (mvo.modelViewOpt.gdlOptions != nullptr) {
            ACAPI_FreeGDLModelViewOptionsPtr (&mvo.modelViewOpt.gdlOptions);
        }
        if (match) {
            return true;
        }
    }
    return false;
}

bool FindDimensionStandard (const GS::UniString& name, API_DimensionPrefs* prefs)
{
    if (name.IsEmpty ()) {
        return false;
    }
    API_DimensionStandardsType dim = {};
    GS::UniString searchName = name;
    dim.head.guid = APINULLGuid;
    dim.head.uniStringNamePtr = &searchName;
    if (ACAPI_Navigator_DimStand_Search (&dim.head) != NoError) {
        return false;
    }
    if (prefs != nullptr) {
        if (ACAPI_Navigator_DimStand_Get (dim) != NoError) {
            return false;
        }
        *prefs = dim.dim;
    }
    return true;
}

void FreeView (API_NavigatorView& view)
{
    if (view.modelViewOpt != nullptr) {
        if (view.modelViewOpt->gdlOptions != nullptr) {
            ACAPI_FreeGDLModelViewOptionsPtr (&view.modelViewOpt->gdlOptions);
        }
        delete view.modelViewOpt;
        view.modelViewOpt = nullptr;
    }
    delete view.dimPrefs;
    view.dimPrefs = nullptr;
    delete view.layerStats;
    view.layerStats = nullptr;
    delete view.pens;
    view.pens = nullptr;
}

API_NavigatorItem MapRoot (API_NavigatorMapID mapId)
{
    API_NavigatorItem root = {};
    API_NavigatorSet set = {};
    set.mapId = mapId;
    Int32 index = 0;
    if (ACAPI_Navigator_GetNavigatorSet (&set, &index) == NoError) {
        ACAPI_Navigator_GetNavigatorItem (&set.rootGuid, &root);
    }
    root.mapId = mapId;
    return root;
}

GS::Array<API_NavigatorItem> Children (API_NavigatorItem item, API_NavigatorMapID mapId)
{
    item.mapId = mapId;
    GS::Array<API_NavigatorItem> children;
    ACAPI_Navigator_GetNavigatorChildrenItems (&item, &children);
    for (API_NavigatorItem& child : children) {
        child.mapId = mapId;
    }
    return children;
}

GS::UniString ViewKey (const GS::Array<GS::UniString>& path, const GS::UniString& name)
{
    GS::UniString key;
    for (const GS::UniString& folder : path) {
        key += folder + "/";
    }
    return key + name;
}

void CaptureViewItems (const API_NavigatorItem& item, GS::Array<GS::UniString> path, const std::function<void (const GS::ObjectState&)>& add, Int32& count, Int32& skipped)
{
    for (const API_NavigatorItem& child : Children (item, API_PublicViewMap)) {
        const GS::UniString name (child.uName);
        if (child.itemType == API_FolderNavItem) {
            GS::Array<GS::UniString> childPath = path;
            childPath.Push (name);
            CaptureViewItems (child, childPath, add, count, skipped);
            continue;
        }
        if (child.itemType != API_StoryNavItem) {
            ++skipped;
            continue;
        }
        API_NavigatorItem viewItem = child;
        API_NavigatorView view = {};
        if (ACAPI_Navigator_GetNavigatorView (&viewItem, &view) != NoError) {
            ++skipped;
            continue;
        }
        GS::ObjectState os;
        const auto& pathList = os.AddList<GS::UniString> ("path");
        for (const GS::UniString& folder : path) {
            pathList (folder);
        }
        os.Add ("name", name);
        os.Add ("customName", child.customName);
        os.Add ("uiId", GS::UniString (child.uiId));
        os.Add ("customUiId", child.customUiId);
        os.Add ("floorNum", static_cast<Int32> (child.floorNum));
        os.Add ("layerCombination", GS::UniString (view.layerCombination));
        os.Add ("saveLaySet", view.saveLaySet);
        os.Add ("drawingScale", view.drawingScale);
        os.Add ("saveDScale", view.saveDScale);
        os.Add ("penSetName", GS::UniString (view.penSetName));
        os.Add ("savePenSet", view.savePenSet);
        os.Add ("graphicOverrideCombination", GS::UniString (view.overrideCombination));
        os.Add ("structureDisplay", static_cast<Int32> (view.structureDisplay));
        os.Add ("saveStructureDisplay", view.saveStructureDisplay);
        os.Add ("renovationFilterGuid", APIGuidToString (view.renovationFilterGuid));
        const GS::UniString mvoName (view.modelViewOptName);
        os.Add ("modelViewOptName", mvoName);
        os.Add ("saveDispOpt", view.saveDispOpt);
        API_ModelViewOptions options = {};
        if (view.modelViewOpt != nullptr) {
            options = *view.modelViewOpt;
            options.gdlOptions = nullptr;
            os.Add ("modelViewOptions", ToHex (options));
        } else if (FindModelViewOptions (mvoName, &options)) {
            os.Add ("modelViewOptions", ToHex (options));
        }
        const GS::UniString dimName (view.dimName);
        os.Add ("dimName", dimName);
        os.Add ("saveDim", view.saveDim);
        API_DimensionPrefs prefs = {};
        if (view.dimPrefs != nullptr) {
            os.Add ("dimensionPrefs", ToHex (*view.dimPrefs));
        } else if (FindDimensionStandard (dimName, &prefs)) {
            os.Add ("dimensionPrefs", ToHex (prefs));
        }
        FreeView (view);
        add (os);
        ++count;
    }
}

bool FindStory (const API_NavigatorItem& item, short floorNum, API_Guid& guid)
{
    for (const API_NavigatorItem& child : Children (item, API_ProjectMap)) {
        if (child.itemType == API_StoryNavItem && child.floorNum == floorNum) {
            guid = child.guid;
            return true;
        }
        if (FindStory (child, floorNum, guid)) {
            return true;
        }
    }
    return false;
}

void CollectViewKeys (const API_NavigatorItem& item, GS::Array<GS::UniString> path, std::map<GS::UniString, bool>& keys)
{
    for (const API_NavigatorItem& child : Children (item, API_PublicViewMap)) {
        const GS::UniString name (child.uName);
        if (child.itemType == API_FolderNavItem) {
            GS::Array<GS::UniString> childPath = path;
            childPath.Push (name);
            CollectViewKeys (child, childPath, keys);
        } else {
            keys[ViewKey (path, name)] = true;
        }
    }
}

// The folder of the path, created where missing. The View Map root itself is never returned: Archicad leaves
// items made directly under it impossible to delete or move.
// deslab: the folders made in this run are remembered by path: a new folder is not found again among the children
// right away, and Archicad then made a numbered copy for every view.
bool EnsureFolder (const GS::Array<GS::UniString>& path, std::map<GS::UniString, API_NavigatorItem>& made, API_Guid& folderGuid)
{
    GS::Array<GS::UniString> folders = path;
    if (folders.IsEmpty ()) {
        folders.Push ("deslab");
    }
    API_NavigatorItem current = MapRoot (API_PublicViewMap);
    bool atRoot = true;
    GS::UniString key;
    for (const GS::UniString& folder : folders) {
        key += folder + "/";
        const auto known = made.find (key);
        if (known != made.end ()) {
            current = known->second;
            atRoot = false;
            continue;
        }
        bool found = false;
        for (const API_NavigatorItem& child : Children (current, API_PublicViewMap)) {
            if (child.itemType == API_FolderNavItem && GS::UniString (child.uName) == folder) {
                current = child;
                found = true;
                break;
            }
        }
        if (!found) {
            API_NavigatorItem folderItem = {};
            folderItem.itemType = API_FolderNavItem;
            folderItem.mapId = API_PublicViewMap;
            folderItem.customName = true;
            GS::ucsncpy (folderItem.uName, folder.ToUStr (), API_UniLongNameLen - 1);
            GS::Guid parent = APIGuid2GSGuid (current.guid);
            if (ACAPI_Navigator_NewNavigatorView (&folderItem, nullptr, atRoot ? nullptr : &parent, nullptr) != NoError) {
                return false;
            }
            folderItem.mapId = API_PublicViewMap;
            current = folderItem;
        }
        made[key] = current;
        atRoot = false;
    }
    folderGuid = current.guid;
    return true;
}

GS::UniString CreateView (const GS::ObjectState& os, std::map<GS::UniString, API_NavigatorItem>& folders)
{
    GS::Array<GS::UniString> path;
    os.Get ("path", path);
    Int32 floorNum = 0;
    os.Get ("floorNum", floorNum);
    API_Guid story = APINULLGuid;
    if (!FindStory (MapRoot (API_ProjectMap), static_cast<short> (floorNum), story)) {
        return GS::UniString::Printf ("no story %d", static_cast<int> (floorNum));
    }
    API_Guid folder = APINULLGuid;
    if (!EnsureFolder (path, folders, folder)) {
        return "folder not created";
    }
    API_Guid created = APINULLGuid;
    GSErrCode err = ACAPI_Navigator_CloneProjectMapItemToViewMap (&story, &folder, &created);
    if (err != NoError) {
        return GS::UniString::Printf ("clone %d", static_cast<int> (err));
    }
    API_NavigatorItem item = {};
    ACAPI_Navigator_GetNavigatorItem (&created, &item);
    item.mapId = API_PublicViewMap;
    item.isIndependent = true;
    bool customName = true;
    os.Get ("customName", customName);
    GS::UniString name;
    if (customName && os.Get ("name", name)) {
        GS::ucsncpy (item.uName, name.ToUStr (), API_UniLongNameLen - 1);
        item.customName = true;
    }
    bool customUiId = false;
    if (os.Get ("customUiId", customUiId) && customUiId) {
        SetCharProperty (&os, "uiId", item.uiId);
        item.customUiId = true;
    }
    ACAPI_Navigator_ChangeNavigatorItem (&item);

    API_NavigatorView view = {};
    err = ACAPI_Navigator_GetNavigatorView (&item, &view);
    if (err != NoError) {
        return GS::UniString::Printf ("view %d", static_cast<int> (err));
    }
    FreeView (view);
    os.Get ("saveLaySet", view.saveLaySet);
    SetCharProperty (&os, "layerCombination", view.layerCombination);
    os.Get ("saveDScale", view.saveDScale);
    os.Get ("drawingScale", view.drawingScale);
    os.Get ("savePenSet", view.savePenSet);
    SetCharProperty (&os, "penSetName", view.penSetName);
    SetUCharProperty (&os, "graphicOverrideCombination", view.overrideCombination);
    Int32 structureDisplay = 0;
    os.Get ("structureDisplay", structureDisplay);
    view.structureDisplay = static_cast<API_StructureDisplay> (structureDisplay);
    os.Get ("saveStructureDisplay", view.saveStructureDisplay);
    GS::UniString renovation;
    if (os.Get ("renovationFilterGuid", renovation)) {
        view.renovationFilterGuid = APIGuidFromString (renovation.ToCStr ());
    }

    GS::UniString mvoName, hex;
    os.Get ("modelViewOptName", mvoName);
    os.Get ("saveDispOpt", view.saveDispOpt);
    if (FindModelViewOptions (mvoName, nullptr)) {
        SetCharProperty (&os, "modelViewOptName", view.modelViewOptName);
    } else if (os.Get ("modelViewOptions", hex)) {
        auto* options = new API_ModelViewOptions ();
        if (FromHex (hex, *options)) {
            options->gdlOptions = nullptr;
            view.modelViewOpt = options;
            view.modelViewOptName[0] = '\0';
        } else {
            delete options;
        }
    }
    GS::UniString dimName;
    os.Get ("dimName", dimName);
    os.Get ("saveDim", view.saveDim);
    if (FindDimensionStandard (dimName, nullptr)) {
        SetCharProperty (&os, "dimName", view.dimName);
    } else if (os.Get ("dimensionPrefs", hex)) {
        auto* prefs = new API_DimensionPrefs ();
        if (FromHex (hex, *prefs)) {
            view.dimPrefs = prefs;
            view.dimName[0] = '\0';
        } else {
            delete prefs;
        }
    }
    err = ACAPI_Navigator_ChangeNavigatorView (&item, &view);
    FreeView (view);
    return err == NoError ? GS::EmptyUniString : GS::UniString::Printf ("settings %d", static_cast<int> (err));
}

void ApplyViews (const GS::ObjectState& package, GS::Array<GS::UniString>& reportLines)
{
    GS::Array<GS::ObjectState> views;
    if (!package.Get ("views", views)) {
        return;
    }
    std::map<GS::UniString, bool> keys;
    CollectViewKeys (MapRoot (API_PublicViewMap), {}, keys);
    Int32 created = 0, existing = 0, failed = 0;
    GS::UniString failures;
    std::map<GS::UniString, API_NavigatorItem> folders;
    for (const GS::ObjectState& os : views) {
        GS::Array<GS::UniString> path;
        GS::UniString name;
        os.Get ("path", path);
        os.Get ("name", name);
        if (keys.find (ViewKey (path, name)) != keys.end ()) {
            ++existing;
            continue;
        }
        const GS::UniString problem = CreateView (os, folders);
        if (problem.IsEmpty ()) {
            ++created;
        } else {
            ++failed;
            if (failed <= 3) {
                failures += "; " + name + " (" + problem + ")";
            }
        }
    }
    reportLines.Push (GS::UniString::Printf ("View: created %d, already there %d, failed %d",
        static_cast<int> (created), static_cast<int> (existing), static_cast<int> (failed)) + failures);
}

// deslab: favorites, exported next to the package as a .prf file and imported with their folders; favorites that the
// open project already has under the same name are kept.
GS::UniString FavoritesPath (const GS::UniString& packagePath)
{
    GS::UniString path = packagePath;
    const UIndex dot = path.FindLast (GS::UniChar ('.'));
    if (dot != MaxUIndex) {
        path.Delete (dot, path.GetLength () - dot);
    }
    return path + "-favorites.prf";
}

void ApplyFavorites (const GS::UniString& packagePath, GS::Array<GS::UniString>& reportLines)
{
    const IO::Location location (FavoritesPath (packagePath));
    bool exists = false;
    if (IO::fileSystem.Contains (location, &exists) != NoError || !exists) {
        return;
    }
    const GSErrCode err = ACAPI_Favorite_Import (location, API_FavoriteFolderHierarchy (), true, API_FavoriteSkip);
    const GS::UniString line = err == NoError ? GS::UniString ("Favorite: imported (existing names kept)")
                                              : GS::UniString::Printf ("Favorite: import error %d", static_cast<int> (err));
    reportLines.Push (line);
}

// deslab: master layouts (size and margins; their drawing content is not copied yet) and the layouts of the Layout
// Book (name, numbering, master), made where the open project has none of that name.
GS::Array<API_DatabaseUnId> Databases (bool masters)
{
    GS::Array<API_DatabaseUnId> ids;
    if (masters) {
        ACAPI_Database_GetMasterLayoutDatabases (nullptr, &ids);
    } else {
        ACAPI_Database_GetLayoutDatabases (nullptr, &ids);
    }
    return ids;
}

bool DatabaseInfo (API_DatabaseUnId id, API_DatabaseInfo& info)
{
    info = {};
    return ACAPI_Window_GetDatabaseInfo (&info, &id) == NoError;
}

void CaptureLayouts (GS::ObjectState& package, GS::ObjectState& counts)
{
    const auto& masters = package.AddList<GS::ObjectState> ("masterLayouts");
    Int32 masterCount = 0;
    for (API_DatabaseUnId id : Databases (true)) {
        API_DatabaseInfo info;
        API_LayoutInfo layout = {};
        if (!DatabaseInfo (id, info) || ACAPI_Navigator_GetLayoutSets (&layout, &id) != NoError) {
            continue;
        }
        masters (GS::ObjectState ("name", GS::UniString (info.name), "ref", GS::UniString (info.ref),
            "sizeX", layout.sizeX, "sizeY", layout.sizeY, "leftMargin", layout.leftMargin, "topMargin", layout.topMargin,
            "rightMargin", layout.rightMargin, "bottomMargin", layout.bottomMargin));
        delete layout.customData;
        ++masterCount;
    }
    const auto& layouts = package.AddList<GS::ObjectState> ("layouts");
    Int32 layoutCount = 0;
    for (API_DatabaseUnId id : Databases (false)) {
        API_DatabaseInfo info, master;
        API_LayoutInfo layout = {};
        if (!DatabaseInfo (id, info) || !DatabaseInfo (info.masterLayoutUnId, master) || ACAPI_Navigator_GetLayoutSets (&layout, &id) != NoError) {
            continue;
        }
        layouts (GS::ObjectState ("name", GS::UniString (info.name), "ref", GS::UniString (info.ref),
            "master", GS::UniString (master.name), "customLayoutNumber", GS::UniString (layout.customLayoutNumber),
            "customLayoutNumbering", layout.customLayoutNumbering, "doNotIncludeInNumbering", layout.doNotIncludeInNumbering,
            "showMasterBelow", layout.showMasterBelow));
        delete layout.customData;
        ++layoutCount;
    }
    counts.Add ("masterLayouts", masterCount);
    counts.Add ("layouts", layoutCount);
}

void ApplyLayouts (const GS::ObjectState& package, GS::Array<GS::UniString>& reportLines)
{
    GS::Array<GS::ObjectState> masters, layouts;
    if (!package.Get ("masterLayouts", masters)) {
        return;
    }
    package.Get ("layouts", layouts);

    std::map<GS::UniString, API_DatabaseUnId> masterIds;
    for (API_DatabaseUnId id : Databases (true)) {
        API_DatabaseInfo info;
        if (DatabaseInfo (id, info)) {
            masterIds[GS::UniString (info.name)] = id;
        }
    }
    Int32 created = 0, existing = 0, failed = 0;
    for (const GS::ObjectState& os : masters) {
        GS::UniString name, ref;
        os.Get ("name", name);
        os.Get ("ref", ref);
        if (masterIds.find (name) != masterIds.end ()) {
            ++existing;
            continue;
        }
        API_DatabaseInfo info = {};
        info.typeID = APIWind_MasterLayoutID;
        GS::ucsncpy (info.name, name.ToUStr (), API_UniLongNameLen - 1);
        GS::ucsncpy (info.ref, ref.ToUStr (), API_UniLongNameLen - 1);
        if (ACAPI_Database_NewDatabase (&info) != NoError) {
            ++failed;
            continue;
        }
        API_LayoutInfo layout = {};
        if (ACAPI_Navigator_GetLayoutSets (&layout, &info.databaseUnId) == NoError) {
            delete layout.customData;
            layout.customData = nullptr;
            os.Get ("sizeX", layout.sizeX);
            os.Get ("sizeY", layout.sizeY);
            os.Get ("leftMargin", layout.leftMargin);
            os.Get ("topMargin", layout.topMargin);
            os.Get ("rightMargin", layout.rightMargin);
            os.Get ("bottomMargin", layout.bottomMargin);
            ACAPI_Navigator_ChangeLayoutSets (&layout, &info.databaseUnId);
        }
        masterIds[name] = info.databaseUnId;
        ++created;
    }
    reportLines.Push (GS::UniString::Printf ("MasterLayout: created %d, already there %d, failed %d (drawing content not copied)",
        static_cast<int> (created), static_cast<int> (existing), static_cast<int> (failed)));

    std::map<GS::UniString, bool> layoutNames;
    for (API_DatabaseUnId id : Databases (false)) {
        API_DatabaseInfo info;
        if (DatabaseInfo (id, info)) {
            layoutNames[GS::UniString (info.name)] = true;
        }
    }
    created = existing = failed = 0;
    GS::UniString failures;
    for (const GS::ObjectState& os : layouts) {
        GS::UniString name, masterName;
        os.Get ("name", name);
        os.Get ("master", masterName);
        if (layoutNames.find (name) != layoutNames.end ()) {
            ++existing;
            continue;
        }
        const auto master = masterIds.find (masterName);
        if (master == masterIds.end ()) {
            ++failed;
            failures += "; " + name + " (no master)";
            continue;
        }
        API_DatabaseUnId masterId = master->second;
        API_LayoutInfo layout = {};
        ACAPI_Navigator_GetLayoutSets (&layout, &masterId);
        delete layout.customData;
        layout.customData = nullptr;
        GS::ucsncpy (layout.layoutName, name.ToUStr (), API_UniLongNameLen - 1);
        SetCharProperty (&os, "customLayoutNumber", layout.customLayoutNumber);
        os.Get ("customLayoutNumbering", layout.customLayoutNumbering);
        os.Get ("doNotIncludeInNumbering", layout.doNotIncludeInNumbering);
        os.Get ("showMasterBelow", layout.showMasterBelow);
        const GSErrCode err = ACAPI_Navigator_CreateLayout (&layout, &masterId);
        if (err == NoError) {
            ++created;
        } else {
            ++failed;
            failures += "; " + name + GS::UniString::Printf (" (%d)", static_cast<int> (err));
        }
    }
    reportLines.Push (GS::UniString::Printf ("Layout: created %d, already there %d, failed %d",
        static_cast<int> (created), static_cast<int> (existing), static_cast<int> (failed)) + failures);
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
                                                       GS::ProcessControl& processControl, GS::Array<GS::UniString>& reportLines)
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
    GuidMap guids;
    std::map<API_AttrTypeID, IndexMap> maps;
    const GSErrCode err = ACAPI_CallUndoableCommand ("Apply office standard", [&] () -> GSErrCode {
        for (const StandardType& type : standardTypes) {
            const TypeResult result = ApplyType (source, hasPackage ? &package : nullptr, type.typeID, maps, guids);
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
    if (err == NoError && hasPackage) {
        GS::Array<GS::ObjectState> attributeNames;
        package.Get ("attributeNames", attributeNames);
        std::map<API_AttrTypeID, std::map<GS::UniString, API_AttributeIndex>> projectNames;
        for (const GS::ObjectState& entry : attributeNames) {
            Int32 type = 0;
            GS::UniString name, guid;
            entry.Get ("type", type);
            entry.Get ("name", name);
            entry.Get ("guid", guid);
            const API_AttrTypeID typeID = static_cast<API_AttrTypeID> (type);
            if (projectNames.find (typeID) == projectNames.end ()) {
                projectNames[typeID] = ProjectNames (typeID);
            }
            const auto it = projectNames[typeID].find (name);
            if (it != projectNames[typeID].end ()) {
                const GS::UniString target = APIGuidToString (GetAttributeGuidFromIndex (typeID, it->second)).ToUpperCase ();
                if (target != guid) {
                    guids[guid] = target;
                }
            }
        }
        // Separate undo steps: the override commands open their own.
        ApplyGraphicOverrides (package, guids, maps[API_LayerID], processControl, reportLines);
        ApplyViews (package, reportLines);
        ApplyFavorites (packagePath, reportLines);
        ApplyLayouts (package, reportLines);
    }
    return err;
#else
    UNUSED_PARAMETER (sourceProjectPath);
    UNUSED_PARAMETER (packagePath);
    UNUSED_PARAMETER (processControl);
    reportLines.Push ("ApplyOfficeStandard needs Archicad 27 or later.");
    return APIERR_GENERAL;
#endif
}

GS::ObjectState ApplyOfficeStandardCommand::Execute (const GS::ObjectState& parameters, GS::ProcessControl& processControl) const
{
    GS::UniString sourceProjectPath;
    if (!parameters.Get ("sourceProjectPath", sourceProjectPath) || sourceProjectPath.IsEmpty ()) {
        return CreateErrorResponse (APIERR_BADPARS, "sourceProjectPath is missing.");
    }
    GS::UniString packagePath;
    parameters.Get ("packagePath", packagePath);
    GS::Array<GS::UniString> reportLines;
    const GSErrCode err = ApplyAttributes (sourceProjectPath, packagePath, processControl, reportLines);
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
            "layerCombinations": { "type": "integer" },
            "graphicOverrideRules": { "type": "integer" },
            "graphicOverrideCombinations": { "type": "integer" },
            "views": { "type": "integer" },
            "viewsSkipped": { "type": "integer", "description": "Views that are not story views (not captured yet)." },
            "favoritesExported": { "type": "boolean", "description": "The favorites were written next to the package (<name>-favorites.prf)." },
            "masterLayouts": { "type": "integer" },
            "layouts": { "type": "integer" }
        },
        "additionalProperties": false,
        "required": ["layers", "surfaces", "buildingMaterials", "layerCombinations"]
    })";
}

GSErrCode CaptureOfficeStandardCommand::Capture (const GS::UniString& packagePath, GS::ProcessControl& processControl, GS::ObjectState& counts)
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
    // deslab: the guids and names of the attributes a graphic override style can point at. The guids read from the
    // standard's file are not always the ones the open project knew, so the rules' guids are resolved by name.
    {
        const auto& names = package.AddList<GS::ObjectState> ("attributeNames");
        const API_AttrTypeID referenced[] = { API_LinetypeID, API_FilltypeID, API_MaterialID, API_BuildingMaterialID, API_LayerID };
        for (API_AttrTypeID typeID : referenced) {
            for (const auto& kv : ProjectNames (typeID)) {
                names (GS::ObjectState ("type", static_cast<Int32> (typeID), "name", kv.first,
                    "guid", APIGuidToString (GetAttributeGuidFromIndex (typeID, kv.second)).ToUpperCase ()));
            }
        }
    }
    // deslab: the graphic override rules and combinations, as GetGraphicOverrideRules/Combinations return them.
    GS::Array<GS::ObjectState> rules, combinations;
    GetGraphicOverrideRulesCommand ().Execute (GS::ObjectState (), processControl).Get ("graphicOverrideRules", rules);
    GetGraphicOverrideCombinationsCommand ().Execute (GS::ObjectState (), processControl).Get ("graphicOverrideCombinations", combinations);
    package.Add ("graphicOverrideRules", rules);
    package.Add ("graphicOverrideCombinations", combinations);
    counts.Add ("graphicOverrideRules", static_cast<Int32> (rules.GetSize ()));
    counts.Add ("graphicOverrideCombinations", static_cast<Int32> (combinations.GetSize ()));
    Int32 viewCount = 0, skippedViews = 0;
    const auto& viewList = package.AddList<GS::ObjectState> ("views");
    CaptureViewItems (MapRoot (API_PublicViewMap), {}, [&] (const GS::ObjectState& os) { viewList (os); }, viewCount, skippedViews);
    counts.Add ("views", viewCount);
    counts.Add ("viewsSkipped", skippedViews);
    CaptureLayouts (package, counts);
    const IO::Location favorites (FavoritesPath (packagePath));
    counts.Add ("favoritesExported", ACAPI_Favorite_Export (favorites, nullptr) == NoError);
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
    UNUSED_PARAMETER (processControl);
    UNUSED_PARAMETER (counts);
    return APIERR_GENERAL;
#endif
}

GS::ObjectState CaptureOfficeStandardCommand::Execute (const GS::ObjectState& parameters, GS::ProcessControl& processControl) const
{
    GS::UniString packagePath;
    if (!parameters.Get ("packagePath", packagePath) || packagePath.IsEmpty ()) {
        return CreateErrorResponse (APIERR_BADPARS, "packagePath is missing.");
    }
    GS::ObjectState counts;
    const GSErrCode err = Capture (packagePath, processControl, counts);
    if (err != NoError) {
        return CreateErrorResponse (err, "Failed to write the standard package.");
    }
    return counts;
}
