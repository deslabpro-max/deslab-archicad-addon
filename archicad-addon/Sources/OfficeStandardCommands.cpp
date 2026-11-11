// deslab: see OfficeStandardCommands.hpp and README.md.

#include "OfficeStandardCommands.hpp"
#include "MigrationHelper.hpp"
#include "GraphicOverrideCommands.hpp"
#include "ElementCreationCommands.hpp"
#include "FileSystem.hpp"
#include "File.hpp"
#include "Folder.hpp"
#include "ObjectStateJSONConversion.hpp"

#include <cstdlib>
#include <cstring>
#include <fstream>
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

struct TypeResult { Int32 created = 0; Int32 existing = 0; Int32 updated = 0; Int32 failed = 0; GS::Array<GS::UniString> failures; GSErrCode readError = NoError; };

void NoteGuid (GuidMap& guids, const API_Guid& sourceGuid, API_AttrTypeID typeID, API_AttributeIndex index)
{
    const API_Guid target = GetAttributeGuidFromIndex (typeID, index);
    if (sourceGuid != APINULLGuid && target != APINULLGuid && sourceGuid != target) {
        guids[APIGuidToString (sourceGuid).ToUpperCase ()] = APIGuidToString (target).ToUpperCase ();
    }
}

TypeResult ApplyType (const IO::Location& source, const GS::ObjectState* package, API_AttrTypeID typeID,
                      std::map<API_AttrTypeID, IndexMap>& maps, GuidMap& guids, bool updateExisting)
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
            // deslab: 08.11.2026, in a new file Archicad's own «Бетон - Конструкционный» kept its green cut fill pen
            // (169) instead of the template's black one: in a new (untitled) project the same-named attributes take the
            // standard's settings, keeping their own index and guid
            if (updateExisting && RemapReferences (attribute, defs, maps)) {
                attribute.header.index = byName->second;
                attribute.header.guid = GetAttributeGuidFromIndex (typeID, byName->second);
                attribute.header.uniStringNamePtr = &name;
                const GSErrCode err = ACAPI_Attribute_ModifyExt (&attribute, &defs);
                if (err == NoError) {
                    ++result.updated;
                } else if (result.failures.GetSize () < 5) {
                    result.failures.Push (name + GS::UniString::Printf (" (not updated %d)", static_cast<int> (err)));
                }
            }
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

// deslab: the style without its fill background pen, the other fields kept with their types.
GS::ObjectState WithoutFillBackground (const GS::ObjectState& style)
{
    GS::ObjectState out;
    for (const char* key : { "lineType", "fillType", "surface", "surfaceColor", "fillTypeAppliesTo", "fillForegroundPenAppliesTo",
                             "fillBackgroundPenAppliesTo", "surfaceAppliesTo", "hiddenContours" }) {
        const GS::ObjectState* child = style.Get (key);
        if (child != nullptr) {
            out.Add (key, *child);
        }
    }
    for (const char* key : { "lineMarkerTextPen", "fillForegroundPen" }) {
        Int32 value = 0;
        if (style.Get (key, value)) {
            out.Add (key, value);
        }
    }
    for (const char* key : { "showSkinSeparators", "overridePenColorAndThickness", "overrideContours" }) {
        bool value = false;
        if (style.Get (key, value)) {
            out.Add (key, value);
        }
    }
    return out;
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
        GS::ObjectState response = CreateGraphicOverrideRulesCommand ().Execute (parameters, processControl);
        GS::UniString newGuid = CreatedGuid (response, "graphicOverrideRuleIds", "graphicOverrideRuleId");
        Int32 backgroundPen = -1;
        GS::ObjectState style;
        if (newGuid.IsEmpty () && rule.Get ("style", style) && style.Get ("fillBackgroundPen", backgroundPen) && backgroundPen == 0) {
            // deslab: Archicad reads a transparent fill background as pen 0 but refuses pen 0 when a rule is made.
            // The rule is made without it, then the background is set on the made rule; when that is refused too,
            // the rule stays without a background override and the report says so.
            {
                const GS::ObjectState plain = WithoutFillBackground (style);
                GS::ObjectState retryRule;
                GS::UniString text;
                if (rule.Get ("name", text)) {
                    retryRule.Add ("name", text);
                }
                if (rule.Get ("ruleGroupName", text)) {
                    retryRule.Add ("ruleGroupName", text);
                }
                if (rule.Get ("criterionXML", text)) {
                    retryRule.Add ("criterionXML", text);
                }
                retryRule.Add ("style", plain);
                GS::ObjectState retryParameters;
                retryParameters.Add ("graphicOverrideRules", GS::Array<GS::ObjectState> { retryRule });
                response = CreateGraphicOverrideRulesCommand ().Execute (retryParameters, processControl);
                newGuid = CreatedGuid (response, "graphicOverrideRuleIds", "graphicOverrideRuleId");
                if (!newGuid.IsEmpty ()) {
                    GS::ObjectState modify;
                    modify.Add ("graphicOverrideRuleId", GS::ObjectState ("guid", newGuid));
                    modify.Add ("style", style);
                    GS::ObjectState modifyParameters;
                    modifyParameters.Add ("graphicOverrideRules", GS::Array<GS::ObjectState> { modify });
                    const GS::ObjectState modified = ModifyGraphicOverrideRulesCommand ().Execute (modifyParameters, processControl);
                    GS::Array<GS::ObjectState> results;
                    bool success = false;
                    if (!modified.Get ("executionResults", results) || results.IsEmpty () || !results[0].Get ("success", success) || !success) {
                        failures += "; " + name + " (made without the transparent fill background)";
                    }
                }
            }
        }
        if (!newGuid.IsEmpty ()) {
            ruleGuids[sourceGuid] = newGuid;
            ++created;
        } else {
            ++failed;
            Int32 code = 0;
            GS::UniString message;
            GS::Array<GS::ObjectState> items;
            GS::ObjectState error;
            if (response.Get ("graphicOverrideRuleIds", items) && !items.IsEmpty () && items[0].Get ("error", error)) {
                error.Get ("code", code);
                error.Get ("message", message);
            }
            // deslab: the message says which attribute of the style was not found.
            failures += "; " + name + GS::UniString::Printf (" (%d: ", static_cast<int> (code)) + message + ")";
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
// custom view settings (the raw Archicad 27 structures, hex encoded; the GDL part of the model view options goes
// separately, by parameter name, see GdlOptionsToOS).
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

// deslab: 11.11.2026, the library part of the model view options (the settings Archicad's library objects read: how a
// zone stamp, a door or a window marker shows) travels too. A new project showed no zone stamp with the same stamp,
// category and parameters as the standard: only this part differed. Each option set is written as its parameter
// values by name; on the open project its own sets are the base and take the values of the parameters they share
// (same name, type and array size). Parameters holding attribute indices other than pens stay as the project has them.
GS::UniString BytesToHex (const unsigned char* bytes, size_t size)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve (size * 2);
    for (size_t i = 0; i < size; ++i) {
        out += digits[bytes[i] >> 4];
        out += digits[bytes[i] & 0xF];
    }
    return GS::UniString (out.c_str ());
}

bool GdlParamTravels (API_AddParID type)
{
    switch (type) {
        case APIParT_Integer: case APIParT_Length: case APIParT_Angle: case APIParT_RealNum: case APIParT_LightSw:
        case APIParT_ColRGB: case APIParT_Intens: case APIParT_PenCol: case APIParT_CString: case APIParT_Boolean:
            return true;
        default:
            return false;
    }
}

UInt32 GdlOptionsCount (const API_GDLModelViewOptions* gdlOptions)
{
    return gdlOptions == nullptr ? 0 : static_cast<UInt32> (BMGetPtrSize (reinterpret_cast<GSConstPtr> (gdlOptions)) / sizeof (API_GDLModelViewOptions));
}

void GdlOptionsToOS (const API_GDLModelViewOptions* gdlOptions, GS::ObjectState& os)
{
    const UInt32 count = GdlOptionsCount (gdlOptions);
    if (count == 0) {
        return;
    }
    const auto& list = os.AddList<GS::ObjectState> ("gdlOptions");
    for (UInt32 i = 0; i < count; ++i) {
        const API_GDLModelViewOptions& option = gdlOptions[i];
        GS::ObjectState optionOS ("name", GS::UniString (option.name), "guid", APIGuidToString (option.guid));
        const auto& params = optionOS.AddList<GS::ObjectState> ("params");
        const UInt32 paramCount = option.params == nullptr ? 0 : static_cast<UInt32> (BMGetHandleSize (reinterpret_cast<GSHandle> (option.params)) / sizeof (API_AddParType));
        for (UInt32 k = 0; k < paramCount; ++k) {
            const API_AddParType& param = (*option.params)[k];
            if (!GdlParamTravels (param.typeID)) {
                continue;
            }
            GS::ObjectState paramOS ("name", GS::UniString (param.name), "type", static_cast<Int32> (param.typeID));
            if (param.typeMod == API_ParArray) {
                if (param.value.array == nullptr) {
                    continue;
                }
                paramOS.Add ("dim1", param.dim1);
                paramOS.Add ("dim2", param.dim2);
                paramOS.Add ("array", BytesToHex (reinterpret_cast<const unsigned char*> (*param.value.array), static_cast<size_t> (BMGetHandleSize (param.value.array))));
            } else if (param.typeID == APIParT_CString) {
                paramOS.Add ("text", GS::UniString (param.value.uStr));
            } else {
                paramOS.Add ("real", param.value.real);
            }
            params (paramOS);
        }
        list (optionOS);
    }
}

// deslab: looked up by index: the search by Unicode name does not find the template's sets.
bool FindModelViewOptions (const GS::UniString& name, API_ModelViewOptions* options, GS::ObjectState* gdl = nullptr)
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
        if (match && gdl != nullptr) {
            GdlOptionsToOS (mvo.modelViewOpt.gdlOptions, *gdl);
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
        view.modelViewOpt = nullptr;
    }
    // deslab: 11.11.2026, the two blocks themselves are let go, not released: for a view with its own model view
    // options Archicad hands out its own block, `delete` on it corrupted the heap and Archicad went down, and the
    // DevKit does not say which call releases it. A few hundred bytes per view.
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
            GdlOptionsToOS (view.modelViewOpt->gdlOptions, os);
            options = *view.modelViewOpt;
            options.gdlOptions = nullptr;
            os.Add ("modelViewOptions", ToHex (options));
        } else if (FindModelViewOptions (mvoName, &options, &os)) {
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
    // deslab: 08.11.2026, a second run nested the whole folder tree inside its own top folder: the map root
    // can be the item that carries the first folder's name, then that name is the root itself
    const bool rootIsFirst = folders.GetSize () > 1 && GS::UniString (current.uName) == folders[0];
    for (UIndex i = rootIsFirst ? 1 : 0; i < folders.GetSize (); ++i) {
        const GS::UniString& folder = folders[i];
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

// deslab: 11.11.2026, a trace of the risky steps (%TEMP%\\deslab-apply-trace.log, flushed line by line): two builds went
// down inside Archicad while the views were made, and a crash leaves no report.
void Trace (const GS::UniString& line)
{
    const char* temp = std::getenv ("TEMP");
    if (temp == nullptr) {
        return;
    }
    std::ofstream out (std::string (temp) + "\\deslab-apply-trace.log", std::ios::app);
    out << line.ToCStr (CC_UTF8).Get () << std::endl;
}

// deslab: 11.11.2026, Archicad 27 does not take a view's own model view options from ChangeNavigatorView: the view
// kept the combination that was current when it was made (a stock one with «hide zone stamp» on, so no zone stamp
// showed). A new view takes the options of the window, so the window gets the standard's options while such views are
// made and its own back after. The library part of the options (gdlOptions) stays the window's: writing the
// standard's values into it is left out until it is proven safe.
struct ViewOptionsSwap {
    bool active = false;
    bool haveBefore = false;
    GS::UniString hex;
    API_ViewOptions before = {};
    short beforeIndex = 0;
    GSErrCode err = NoError;
};

void RestoreViewOptions (ViewOptionsSwap& swap)
{
    if (swap.active) {
        Trace ("ChangeViewOptions: back");
        ACAPI_Navigator_ChangeViewOptions (&swap.before, swap.beforeIndex > 0 ? &swap.beforeIndex : nullptr);
        Trace ("ChangeViewOptions: back done");
        swap.active = false;
        swap.hex.Clear ();
    }
}

// True when the window now has the options this view wants as its own.
bool WantViewOptions (ViewOptionsSwap& swap, const GS::ObjectState& os)
{
    GS::UniString name, hex;
    os.Get ("modelViewOptName", name);
    API_ModelViewOptions custom = {};
    swap.err = NoError;
    if (FindModelViewOptions (name, nullptr) || !os.Get ("modelViewOptions", hex) || !FromHex (hex, custom)) {
        RestoreViewOptions (swap);
        return false;
    }
    if (swap.active && swap.hex == hex) {
        return true;
    }
    if (!swap.haveBefore) {
        Trace ("GetViewOptions");
        swap.err = ACAPI_Navigator_GetViewOptions (&swap.before, &swap.beforeIndex);
        Trace (GS::UniString::Printf ("GetViewOptions done %d, index %d", static_cast<int> (swap.err), static_cast<int> (swap.beforeIndex)));
        if (swap.err != NoError) {
            return false;
        }
        swap.haveBefore = true;
    }
    API_ViewOptions wanted = swap.before;
    wanted.modelViewOpt = custom;
    wanted.modelViewOpt.gdlOptions = swap.before.modelViewOpt.gdlOptions;
    Trace ("ChangeViewOptions: standard");
    swap.err = ACAPI_Navigator_ChangeViewOptions (&wanted);
    Trace (GS::UniString::Printf ("ChangeViewOptions: standard done %d", static_cast<int> (swap.err)));
    if (swap.err != NoError) {
        return false;
    }
    swap.active = true;
    swap.hex = hex;
    return true;
}

GS::UniString CreateView (const GS::ObjectState& os, std::map<GS::UniString, API_NavigatorItem>& folders, ViewOptionsSwap& swap)
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
    const bool swapped = WantViewOptions (swap, os);
    const GSErrCode swapErr = swapped ? NoError : swap.err;
    GS::UniString traceName;
    os.Get ("name", traceName);
    Trace ("Clone: " + traceName);
    API_Guid created = APINULLGuid;
    GSErrCode err = ACAPI_Navigator_CloneProjectMapItemToViewMap (&story, &folder, &created);
    Trace (GS::UniString::Printf ("Clone done %d", static_cast<int> (err)));
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
    if (swapped) {
        view.dimPrefs = nullptr;   // the model view options the view was made with stay
    } else {
        FreeView (view);
    }
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
    if (swapped) {
        // made with the standard's options above
    } else if (FindModelViewOptions (mvoName, nullptr)) {
        SetCharProperty (&os, "modelViewOptName", view.modelViewOptName);
    } else if (os.Get ("modelViewOptions", hex)) {
        auto* options = reinterpret_cast<API_ModelViewOptions*> (BMAllocatePtr (sizeof (API_ModelViewOptions), ALLOCATE_CLEAR, 0));
        if (options != nullptr && FromHex (hex, *options)) {
            options->gdlOptions = nullptr;
            view.modelViewOpt = options;
            view.modelViewOptName[0] = '\0';
        } else if (options != nullptr) {
            BMKillPtr (reinterpret_cast<GSPtr*> (&options));
        }
    }
    GS::UniString dimName;
    os.Get ("dimName", dimName);
    os.Get ("saveDim", view.saveDim);
    if (FindDimensionStandard (dimName, nullptr)) {
        SetCharProperty (&os, "dimName", view.dimName);
    } else if (os.Get ("dimensionPrefs", hex)) {
        auto* prefs = reinterpret_cast<API_DimensionPrefs*> (BMAllocatePtr (sizeof (API_DimensionPrefs), ALLOCATE_CLEAR, 0));
        if (prefs != nullptr && FromHex (hex, *prefs)) {
            view.dimPrefs = prefs;
            view.dimName[0] = '\0';
        } else if (prefs != nullptr) {
            BMKillPtr (reinterpret_cast<GSPtr*> (&prefs));
        }
    }
    Trace ("ChangeNavigatorView");
    err = ACAPI_Navigator_ChangeNavigatorView (&item, &view);
    Trace (GS::UniString::Printf ("ChangeNavigatorView done %d", static_cast<int> (err)));
    FreeView (view);
    Trace ("FreeView done");
    if (err == NoError && swapErr != NoError) {
        return GS::UniString::Printf ("made, but its model view options were not set (%d)", static_cast<int> (swapErr));
    }
    return err == NoError ? GS::EmptyUniString : GS::UniString::Printf ("settings %d", static_cast<int> (err));
}

// deslab: 08.11.2026, a second «Построить» made all 29 views again: the existing ones were not recognised. A view
// counts as there when its folder path and name match the end of an existing one, or the other way round
// (the map root may or may not carry the top folder's name).
bool ViewExists (const std::map<GS::UniString, bool>& keys, const GS::Array<GS::UniString>& path, const GS::UniString& name)
{
    const GS::UniString key = ViewKey (path, name);
    for (const auto& existing : keys) {
        const GS::UniString& other = existing.first;
        if (other == key || other.EndsWith ("/" + key) || key.EndsWith ("/" + other)) {
            return true;
        }
    }
    return false;
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
    ViewOptionsSwap swap;
    Trace ("ApplyViews");
    for (const GS::ObjectState& os : views) {
        GS::Array<GS::UniString> path;
        GS::UniString name;
        os.Get ("path", path);
        os.Get ("name", name);
        if (ViewExists (keys, path, name)) {
            ++existing;
            continue;
        }
        const GS::UniString problem = CreateView (os, folders, swap);
        if (problem.IsEmpty ()) {
            ++created;
        } else {
            ++failed;
            if (failed <= 3) {
                failures += "; " + name + " (" + problem + ")";
            }
        }
    }
    RestoreViewOptions (swap);
    if (swap.before.modelViewOpt.gdlOptions != nullptr) {
        ACAPI_FreeGDLModelViewOptionsPtr (&swap.before.modelViewOpt.gdlOptions);
    }
    Trace ("ApplyViews done");
    reportLines.Push (GS::UniString::Printf ("View: created %d, already there %d, failed %d",
        static_cast<int> (created), static_cast<int> (existing), static_cast<int> (failed)) + failures);
}

// deslab: 11.11.2026, the office's own library parts (the zone stamp «номер в круге», the logo...) live in the embedded
// library of the standard's project. A new project has none of them, so its zones had no stamp. «Снять стандарт» copies
// the embedded library next to the package (<name>-library), «Оформить проект» copies it into the open project's
// embedded library; files the project already has are kept.
GS::UniString LibraryPath (const GS::UniString& packagePath)
{
    GS::UniString path = packagePath;
    const UIndex dot = path.FindLast (GS::UniChar ('.'));
    if (dot != MaxUIndex) {
        path.Delete (dot, path.GetLength () - dot);
    }
    return path + "-library";
}

bool EmbeddedLibrary (IO::Location& location)
{
    GS::Array<API_LibraryInfo> libraries;
    if (ACAPI_LibraryManagement_GetLibraries (&libraries) != NoError) {
        return false;
    }
    for (const API_LibraryInfo& library : libraries) {
        if (library.libraryType == API_EmbeddedLibrary) {
            location = library.location;
            return !location.IsEmpty ();
        }
    }
    return false;
}

// Copies the tree with plain file operations (to the package). Returns the number of files copied.
Int32 CopyTree (const IO::Location& from, const IO::Location& to)
{
    Int32 copied = 0;
    IO::Folder source (from);
    if (source.GetStatus () != NoError) {
        return 0;
    }
    IO::fileSystem.CreateFolderTree (to);
    GS::Array<GS::Pair<IO::Name, bool>> entries;
    source.Enumerate ([&] (const IO::Name& name, bool isFolder) {
        entries.Push (GS::Pair<IO::Name, bool> (name, isFolder));
    });
    for (const GS::Pair<IO::Name, bool>& entry : entries) {
        const IO::Location child (from, entry.first);
        const IO::Location target (to, entry.first);
        if (entry.second) {
            copied += CopyTree (child, target);
        } else {
            IO::fileSystem.Delete (target);
            if (IO::fileSystem.Copy (child, target) == NoError) {
                ++copied;
            }
        }
    }
    return copied;
}

// Copies the tree into the embedded library through the API, so Archicad knows of every folder and file.
void CopyTreeIntoLibrary (const IO::Location& from, const IO::Location& to, Int32& copied, Int32& kept, Int32& failed)
{
    IO::Folder source (from);
    if (source.GetStatus () != NoError) {
        return;
    }
    GS::Array<IO::Location> files;
    GS::Array<IO::Name> folders;
    source.Enumerate ([&] (const IO::Name& name, bool isFolder) {
        if (isFolder) {
            folders.Push (name);
            return;
        }
        bool exists = false;
        if (IO::fileSystem.Contains (IO::Location (to, name), &exists) == NoError && exists) {
            ++kept;
        } else {
            files.Push (IO::Location (from, name));
        }
    });
    if (!files.IsEmpty ()) {
        const bool overwrite = false;
        if (ACAPI_LibraryManagement_CopyFilesIntoLibrary (&to, &files, &overwrite) == NoError) {
            copied += static_cast<Int32> (files.GetSize ());
        } else {
            failed += static_cast<Int32> (files.GetSize ());
        }
    }
    for (const IO::Name& name : folders) {
        const IO::Location target (to, name);
        if (IO::Folder (target).GetStatus () != NoError && ACAPI_LibraryManagement_CreateFolderInLibrary (&target) != NoError) {
            ++failed;
            continue;
        }
        CopyTreeIntoLibrary (IO::Location (from, name), target, copied, kept, failed);
    }
}

void ApplyEmbeddedLibrary (const GS::UniString& packagePath, GS::Array<GS::UniString>& reportLines)
{
    const IO::Location source (LibraryPath (packagePath));
    if (IO::Folder (source).GetStatus () != NoError) {
        return;
    }
    IO::Location target;
    if (!EmbeddedLibrary (target)) {
        reportLines.Push ("EmbeddedLibrary: the project's embedded library was not found");
        return;
    }
    Int32 copied = 0, kept = 0, failed = 0;
    CopyTreeIntoLibrary (source, target, copied, kept, failed);
    reportLines.Push (GS::UniString::Printf ("EmbeddedLibrary: copied %d, already there %d, failed %d",
        static_cast<int> (copied), static_cast<int> (kept), static_cast<int> (failed)));
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

// deslab: the drawing content of a master layout (the title block): its lines and texts, written per master into the
// package and drawn into the open project's master of that name. Other element types are counted and left out.
GS::UniString LayerName (API_AttributeIndex index)
{
    GS::UniString name;
    API_Attribute attribute = {};
    attribute.header.typeID = API_LayerID;
    attribute.header.index = index;
    attribute.header.uniStringNamePtr = &name;
    ACAPI_Attribute_Get (&attribute);
    DisposeAttribute (attribute);
    return name;
}

GS::ObjectState Coordinate (const API_Coord& c)
{
    return GS::ObjectState ("x", c.x, "y", c.y);
}

// Runs an action with the given database current, then goes back to the one that was.
GSErrCode InDatabase (API_DatabaseUnId id, const std::function<void ()>& action)
{
    API_DatabaseInfo start = {};
    GSErrCode err = ACAPI_Database_GetCurrentDatabase (&start);
    if (err != NoError) {
        return err;
    }
    API_DatabaseInfo target;
    if (!DatabaseInfo (id, target)) {
        return APIERR_BADDATABASE;
    }
    err = ACAPI_Database_ChangeCurrentDatabase (&target);
    if (err != NoError) {
        return err;
    }
    action ();
    return ACAPI_Database_ChangeCurrentDatabase (&start);
}

GS::Array<GS::ObjectState> CaptureMasterContent (API_DatabaseUnId id, Int32& skipped)
{
    GS::Array<GS::ObjectState> out;
    InDatabase (id, [&] () {
        bool savedAutoText = true;
        ACAPI_AutoText_GetAutoTextFlag (&savedAutoText);
        bool keys = false;   // autotexts are read as their keys, not as the text they show here
        ACAPI_AutoText_ChangeAutoTextFlag (&keys);
        GS::Array<API_Guid> guids;
        ACAPI_Element_GetElemList (API_ZombieElemID, &guids);
        for (const API_Guid& guid : guids) {
            API_Element element = {};
            element.header.guid = guid;
            if (ACAPI_Element_Get (&element) != NoError) {
                ++skipped;
                continue;
            }
            const API_ElemTypeID type = GetElemTypeId (element.header);
            if (type == API_LineID) {
                GS::ObjectState os ("type", GS::UniString ("Line"), "layer", LayerName (element.header.layer),
                    "beg", Coordinate (element.line.begC), "end", Coordinate (element.line.endC),
                    "pen", static_cast<Int32> (element.line.linePen.penIndex));
                os.Add ("lineType", APIGuidToString (GetAttributeGuidFromIndex (API_LinetypeID, element.line.ltypeInd)));
                out.Push (os);
            } else if (type == API_TextID) {
                GS::ObjectState style;
                TextLabelDetails::AddTextStyleDetails (style, element.text, false);
                GS::ObjectState os ("type", GS::UniString ("Text"), "layer", LayerName (element.header.layer),
                    "loc", Coordinate (element.text.loc), "style", style);
                if (TextLabelDetails::AddTextContent (os, guid) != NoError) {
                    ++skipped;
                    continue;
                }
                // deslab: 11.11.2026, a text block of a set width wraps its lines (the approval block, 74 mm); without
                // the width the lines ran on and out of the sheet.
                if (!element.text.nonBreaking && element.text.width > 0.0) {
                    os.Add ("wrapWidth", element.text.width);
                }
                out.Push (os);
            } else {
                ++skipped;
            }
        }
        ACAPI_AutoText_ChangeAutoTextFlag (&savedAutoText);
    });
    return out;
}

// Draws the captured content into a master; nothing is drawn into a master that already has elements.
void ApplyMasterContent (API_DatabaseUnId id, const GS::Array<GS::ObjectState>& content, const GuidMap& attributeGuids,
                         GS::ProcessControl& processControl, Int32& drawn, Int32& failed, bool& skippedFilled)
{
    if (content.IsEmpty ()) {
        return;
    }
    const std::map<GS::UniString, API_AttributeIndex> layers = ProjectNames (API_LayerID);
    InDatabase (id, [&] () {
        GS::Array<API_Guid> present;
        ACAPI_Element_GetElemList (API_ZombieElemID, &present);
        if (!present.IsEmpty ()) {
            skippedFilled = true;
            return;
        }
        GS::Array<GS::ObjectState> lines, texts;
        GS::Array<double> wrapWidths;
        for (const GS::ObjectState& item : content) {
            GS::UniString type, layer;
            item.Get ("type", type);
            item.Get ("layer", layer);
            const auto found = layers.find (layer);
            const Int32 layerIndex = found != layers.end () ? GetAttributeIndex (found->second) : 1;
            if (type == "Line") {
                GS::ObjectState beg, end;
                Int32 pen = 1;
                GS::UniString lineType;
                item.Get ("beg", beg);
                item.Get ("end", end);
                item.Get ("pen", pen);
                item.Get ("lineType", lineType);
                const auto mapped = attributeGuids.find (lineType.ToUpperCase ());
                if (mapped != attributeGuids.end ()) {
                    lineType = mapped->second;
                }
                GS::ObjectState line ("begCoordinate", beg, "endCoordinate", end, "linePenIndex", pen, "layerIndex", layerIndex,
                    "floorInd", 0);
                line.Add ("lineTypeId", GS::ObjectState ("guid", lineType));
                lines.Push (line);
            } else if (type == "Text") {
                GS::ObjectState loc, style;
                double x = 0.0, y = 0.0;
                item.Get ("loc", loc);
                loc.Get ("x", x);
                loc.Get ("y", y);
                item.Get ("style", style);
                GS::ObjectState text ("coordinate", GS::ObjectState ("x", x, "y", y, "z", 0.0), "style", style,
                    "floorIndex", 0, "layerIndex", layerIndex);
                GS::Array<GS::ObjectState> runs;
                GS::UniString plain;
                if (item.Get ("runs", runs) && !runs.IsEmpty ()) {
                    text.Add ("runs", runs);
                } else {
                    item.Get ("text", plain);
                    text.Add ("text", plain);
                }
                double wrapWidth = 0.0;
                item.Get ("wrapWidth", wrapWidth);
                wrapWidths.Push (wrapWidth);
                texts.Push (text);
            }
        }
        const auto count = [&] (const GS::ObjectState& response) {
            GS::Array<GS::ObjectState> elements;
            response.Get ("elements", elements);
            for (const GS::ObjectState& element : elements) {
                if (element.Contains ("elementId")) {
                    ++drawn;
                } else {
                    ++failed;
                }
            }
        };
        if (!lines.IsEmpty ()) {
            count (CreateLineElementsCommand ().Execute (GS::ObjectState ("linesData", lines), processControl));
        }
        if (!texts.IsEmpty ()) {
            const GS::ObjectState response = CreateTextsCommand ().Execute (GS::ObjectState ("textsData", texts), processControl);
            count (response);
            GS::Array<GS::ObjectState> elements;
            response.Get ("elements", elements);
            ACAPI_CallUndoableCommand ("deslab text width", [&] () -> GSErrCode {
                for (UIndex i = 0; i < elements.GetSize () && i < wrapWidths.GetSize (); ++i) {
                    GS::ObjectState elementId;
                    GS::UniString guid;
                    if (wrapWidths[i] <= 0.0 || !elements[i].Get ("elementId", elementId) || !elementId.Get ("guid", guid)) {
                        continue;
                    }
                    API_Element element = {};
                    element.header.guid = APIGuidFromString (guid.ToCStr ());
                    if (ACAPI_Element_Get (&element) != NoError) {
                        continue;
                    }
                    API_Element mask;
                    ACAPI_ELEMENT_MASK_CLEAR (mask);
                    element.text.width = wrapWidths[i];
                    element.text.nonBreaking = false;
                    ACAPI_ELEMENT_MASK_SET (mask, API_TextType, width);
                    ACAPI_ELEMENT_MASK_SET (mask, API_TextType, nonBreaking);
                    ACAPI_Element_Change (&element, &mask, nullptr, 0, true);
                }
                return NoError;
            });
        }
    });
}

void CaptureLayouts (GS::ObjectState& package, GS::ObjectState& counts)
{
    const auto& masters = package.AddList<GS::ObjectState> ("masterLayouts");
    Int32 masterCount = 0, contentCount = 0, contentSkipped = 0;
    for (API_DatabaseUnId id : Databases (true)) {
        API_DatabaseInfo info;
        API_LayoutInfo layout = {};
        if (!DatabaseInfo (id, info) || ACAPI_Navigator_GetLayoutSets (&layout, &id) != NoError) {
            continue;
        }
        GS::ObjectState master ("name", GS::UniString (info.name), "ref", GS::UniString (info.ref),
            "sizeX", layout.sizeX, "sizeY", layout.sizeY, "leftMargin", layout.leftMargin, "topMargin", layout.topMargin,
            "rightMargin", layout.rightMargin, "bottomMargin", layout.bottomMargin);
        const GS::Array<GS::ObjectState> content = CaptureMasterContent (id, contentSkipped);
        contentCount += static_cast<Int32> (content.GetSize ());
        master.Add ("content", content);
        masters (master);
        delete layout.customData;
        ++masterCount;
    }
    const auto& layouts = package.AddList<GS::ObjectState> ("layouts");
    Int32 layoutCount = 0, layoutContentCount = 0, layoutContentSkipped = 0;
    for (API_DatabaseUnId id : Databases (false)) {
        API_DatabaseInfo info, master;
        API_LayoutInfo layout = {};
        if (!DatabaseInfo (id, info) || !DatabaseInfo (info.masterLayoutUnId, master) || ACAPI_Navigator_GetLayoutSets (&layout, &id) != NoError) {
            continue;
        }
        GS::ObjectState layoutOS ("name", GS::UniString (info.name), "ref", GS::UniString (info.ref),
            "master", GS::UniString (master.name), "customLayoutNumber", GS::UniString (layout.customLayoutNumber),
            "customLayoutNumbering", layout.customLayoutNumbering, "doNotIncludeInNumbering", layout.doNotIncludeInNumbering,
            "showMasterBelow", layout.showMasterBelow);
        // deslab: 11.11.2026, what the office draws on the layout itself (legend table, note, approval block): its
        // lines and texts, as for a master. Drawings, fills and objects are counted and left out for now.
        const GS::Array<GS::ObjectState> layoutContent = CaptureMasterContent (id, layoutContentSkipped);
        layoutContentCount += static_cast<Int32> (layoutContent.GetSize ());
        layoutOS.Add ("content", layoutContent);
        layouts (layoutOS);
        delete layout.customData;
        ++layoutCount;
    }
    counts.Add ("masterLayouts", masterCount);
    counts.Add ("masterLayoutElements", contentCount);
    counts.Add ("masterLayoutElementsSkipped", contentSkipped);
    counts.Add ("layouts", layoutCount);
    counts.Add ("layoutElements", layoutContentCount);
    counts.Add ("layoutElementsSkipped", layoutContentSkipped);
}

void ApplyLayouts (const GS::ObjectState& package, const GuidMap& attributeGuids, GS::ProcessControl& processControl,
                   GS::Array<GS::UniString>& reportLines)
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
    Int32 created = 0, existing = 0, failed = 0, resized = 0;
    for (const GS::ObjectState& os : masters) {
        GS::UniString name, ref;
        os.Get ("name", name);
        os.Get ("ref", ref);
        const auto found = masterIds.find (name);
        if (found != masterIds.end ()) {
            // deslab: a master of the same name takes the standard's size and margins.
            API_DatabaseUnId id = found->second;
            API_LayoutInfo layout = {};
            if (ACAPI_Navigator_GetLayoutSets (&layout, &id) == NoError) {
                delete layout.customData;
                layout.customData = nullptr;
                const API_LayoutInfo before = layout;
                os.Get ("sizeX", layout.sizeX);
                os.Get ("sizeY", layout.sizeY);
                os.Get ("leftMargin", layout.leftMargin);
                os.Get ("topMargin", layout.topMargin);
                os.Get ("rightMargin", layout.rightMargin);
                os.Get ("bottomMargin", layout.bottomMargin);
                if ((layout.sizeX != before.sizeX || layout.sizeY != before.sizeY || layout.leftMargin != before.leftMargin ||
                     layout.topMargin != before.topMargin || layout.rightMargin != before.rightMargin || layout.bottomMargin != before.bottomMargin) &&
                    ACAPI_Navigator_ChangeLayoutSets (&layout, &id) == NoError) {
                    ++resized;
                }
            }
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
    reportLines.Push (GS::UniString::Printf ("MasterLayout: created %d, already there %d (resized %d), failed %d",
        static_cast<int> (created), static_cast<int> (existing), static_cast<int> (resized), static_cast<int> (failed)));

    Int32 drawn = 0, drawFailed = 0;
    GS::UniString filled;
    for (const GS::ObjectState& os : masters) {
        GS::UniString name;
        GS::Array<GS::ObjectState> content;
        os.Get ("name", name);
        os.Get ("content", content);
        const auto found = masterIds.find (name);
        if (found == masterIds.end () || content.IsEmpty ()) {
            continue;
        }
        bool skippedFilled = false;
        ApplyMasterContent (found->second, content, attributeGuids, processControl, drawn, drawFailed, skippedFilled);
        if (skippedFilled) {
            filled += "; " + name + " already has content";
        }
    }
    reportLines.Push (GS::UniString::Printf ("MasterLayoutContent: drawn %d, failed %d",
        static_cast<int> (drawn), static_cast<int> (drawFailed)) + filled);

    std::map<GS::UniString, bool> layoutNames;
    for (API_DatabaseUnId id : Databases (false)) {
        API_DatabaseInfo info;
        if (DatabaseInfo (id, info)) {
            layoutNames[GS::UniString (info.name)] = true;
        }
    }
    created = existing = failed = 0;
    GS::UniString failures;
    std::map<GS::UniString, bool> createdNames;
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
        // deslab: the standard numbers its layouts through its subsets («04», «05»), which are not copied; a new
        // project numbered them by its own book («AP1»). The number the layout shows in the standard is kept as
        // its own number.
        if (!layout.customLayoutNumbering && SetCharProperty (&os, "ref", layout.customLayoutNumber) && layout.customLayoutNumber[0] != 0) {
            layout.customLayoutNumbering = true;
        }
        os.Get ("doNotIncludeInNumbering", layout.doNotIncludeInNumbering);
        os.Get ("showMasterBelow", layout.showMasterBelow);
        const GSErrCode err = ACAPI_Navigator_CreateLayout (&layout, &masterId);
        if (err == NoError) {
            ++created;
            createdNames[name] = true;
        } else {
            ++failed;
            failures += "; " + name + GS::UniString::Printf (" (%d)", static_cast<int> (err));
        }
    }
    reportLines.Push (GS::UniString::Printf ("Layout: created %d, already there %d, failed %d",
        static_cast<int> (created), static_cast<int> (existing), static_cast<int> (failed)) + failures);

    // deslab: the lines and texts of the layouts made just now (a layout that was there is not drawn on).
    std::map<GS::UniString, API_DatabaseUnId> layoutIds;
    for (API_DatabaseUnId id : Databases (false)) {
        API_DatabaseInfo info;
        if (DatabaseInfo (id, info)) {
            layoutIds[GS::UniString (info.name)] = id;
        }
    }
    Int32 layoutDrawn = 0, layoutDrawFailed = 0;
    for (const GS::ObjectState& os : layouts) {
        GS::UniString name;
        GS::Array<GS::ObjectState> content;
        os.Get ("name", name);
        const auto found = layoutIds.find (name);
        if (createdNames.find (name) == createdNames.end () || found == layoutIds.end () || !os.Get ("content", content)) {
            continue;
        }
        bool skippedFilled = false;
        ApplyMasterContent (found->second, content, attributeGuids, processControl, layoutDrawn, layoutDrawFailed, skippedFilled);
    }
    reportLines.Push (GS::UniString::Printf ("LayoutContent: drawn %d, failed %d", static_cast<int> (layoutDrawn), static_cast<int> (layoutDrawFailed)));
}

// deslab: the pen sets in effect for the model and for the layouts (by name) and the filled project info fields.
// Layouts drew in the open project's own layout pen set (pen 1 red), and the empty project status showed its
// placeholder in the title block.
// deslab: only the project info fields the user fills in (the same prefixes as GetProjectInfoFields); dates, file
// names and layout autotexts are worked out by Archicad and are not copied.
bool IsProjectInfoKey (const GS::UniString& key)
{
    for (const char* prefix : { "PROJECT", "KEYWORD", "NOTES", "SITE", "BUILDING", "CONTACT", "CAD_TECHNICIAN", "CLIENT" }) {
        if (key.BeginsWith (prefix)) {
            return true;
        }
    }
    return false;
}

void CapturePenSetsAndProjectInfo (GS::ObjectState& package, GS::ObjectState& counts)
{
    GS::Array<API_Attribute> tables;
    ACAPI_Attribute_GetAttributesByType (API_PenTableID, tables);
    for (API_Attribute& table : tables) {
        GS::UniString name;
        API_Attribute full = {};
        full.header.typeID = API_PenTableID;
        full.header.index = table.header.index;
        full.header.uniStringNamePtr = &name;
        if (ACAPI_Attribute_Get (&full) == NoError) {
            if (full.penTable.inEffectForModel) {
                package.Add ("penSetForModel", name);
            }
            if (full.penTable.inEffectForLayout) {
                package.Add ("penSetForLayout", name);
            }
        }
        DisposeAttribute (full);
        DisposeAttribute (table);
    }
    GS::Array<GS::ArrayFB<GS::UniString, 3>> autoTexts;
    const auto& fields = package.AddList<GS::ObjectState> ("projectInfo");
    Int32 count = 0;
    if (ACAPI_AutoText_GetAutoTexts (&autoTexts, APIAutoText_All) == NoError) {
        for (const auto& autoText : autoTexts) {
            if (!autoText[2].IsEmpty () && IsProjectInfoKey (autoText[1])) {
                fields (GS::ObjectState ("title", autoText[0], "key", autoText[1], "value", autoText[2]));
                ++count;
            }
        }
    }
    counts.Add ("projectInfoFields", count);
}

void ApplyPenSetsAndProjectInfo (const GS::ObjectState& package, GS::Array<GS::UniString>& reportLines)
{
    GS::UniString report = "PenSet:";
    for (const char* key : { "penSetForModel", "penSetForLayout" }) {
        GS::UniString wanted;
        if (!package.Get (key, wanted)) {
            continue;
        }
        const bool forModel = GS::String (key) == "penSetForModel";
        const std::map<GS::UniString, API_AttributeIndex> names = ProjectNames (API_PenTableID);
        const auto found = names.find (wanted);
        if (found == names.end ()) {
            report += " " + GS::UniString (forModel ? "model" : "layout") + " '" + wanted + "' missing;";
            continue;
        }
        const GSErrCode err = ACAPI_CallUndoableCommand ("deslab: pen set", [&] () -> GSErrCode {
            API_Attribute attribute = {};
            attribute.header.typeID = API_PenTableID;
            attribute.header.index = found->second;
            GSErrCode e = ACAPI_Attribute_Get (&attribute);
            if (e != NoError) {
                return e;
            }
            if ((forModel && attribute.penTable.inEffectForModel) || (!forModel && attribute.penTable.inEffectForLayout)) {
                DisposeAttribute (attribute);
                return NoError;
            }
            API_AttributeDefExt defs = {};
            e = ACAPI_Attribute_GetDefExt (API_PenTableID, found->second, &defs);
            if (e == NoError) {
                if (forModel) {
                    attribute.penTable.inEffectForModel = true;
                } else {
                    attribute.penTable.inEffectForLayout = true;
                }
                e = ACAPI_Attribute_ModifyExt (&attribute, &defs);
            }
            ACAPI_DisposeAttrDefsHdlsExt (&defs);
            DisposeAttribute (attribute);
            return e;
        });
        report += GS::UniString::Printf (" %s '", forModel ? "model" : "layout") + wanted + GS::UniString::Printf ("' (%d);", static_cast<int> (err));
    }
    reportLines.Push (report);

    GS::Array<GS::ObjectState> fields;
    if (!package.Get ("projectInfo", fields)) {
        return;
    }
    std::map<GS::UniString, GS::UniString> current;
    GS::Array<GS::ArrayFB<GS::UniString, 3>> autoTexts;
    if (ACAPI_AutoText_GetAutoTexts (&autoTexts, APIAutoText_All) == NoError) {
        for (const auto& autoText : autoTexts) {
            current[autoText[1]] = autoText[2];
        }
    }
    Int32 set = 0, kept = 0, failed = 0;
    for (const GS::ObjectState& field : fields) {
        GS::UniString key, value;
        field.Get ("key", key);
        field.Get ("value", value);
        const auto it = current.find (key);
        if (it == current.end () || !IsProjectInfoKey (key)) {
            continue;   // a field this project does not have (a custom one) is left out
        }
        if (!it->second.IsEmpty ()) {
            ++kept;     // a field the project has filled in is kept
            continue;
        }
        if (ACAPI_AutoText_SetAnAutoText (&key, &value) == NoError) {
            ++set;
        } else {
            ++failed;
        }
    }
    reportLines.Push (GS::UniString::Printf ("ProjectInfo: set %d, kept %d, failed %d",
        static_cast<int> (set), static_cast<int> (kept), static_cast<int> (failed)));
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
            },
            "updateExisting": {
                "type": "boolean",
                "description": "Optional, default false. Attributes already in the project under the same name take the standard's settings (for a new project, whose Archicad defaults differ from the office template)."
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
                                                       GS::ProcessControl& processControl, GS::Array<GS::UniString>& reportLines,
                                                       bool updateExisting)
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
    if (hasPackage) {
        ApplyEmbeddedLibrary (packagePath, reportLines);
    }
    GuidMap guids;
    std::map<API_AttrTypeID, IndexMap> maps;
    const GSErrCode err = ACAPI_CallUndoableCommand ("Apply office standard", [&] () -> GSErrCode {
        for (const StandardType& type : standardTypes) {
            const TypeResult result = ApplyType (source, hasPackage ? &package : nullptr, type.typeID, maps, guids, updateExisting);
            GS::UniString line = GS::UniString::Printf ("%s: created %d, already there %d (updated %d), failed %d",
                type.name, static_cast<int> (result.created), static_cast<int> (result.existing), static_cast<int> (result.updated),
                static_cast<int> (result.failed));
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
        ApplyLayouts (package, guids, processControl, reportLines);
        ApplyPenSetsAndProjectInfo (package, reportLines);
    }
    return err;
#else
    UNUSED_PARAMETER (sourceProjectPath);
    UNUSED_PARAMETER (packagePath);
    UNUSED_PARAMETER (processControl);
    UNUSED_PARAMETER (updateExisting);
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
    bool updateExisting = false;
    parameters.Get ("updateExisting", updateExisting);
    GS::Array<GS::UniString> reportLines;
    const GSErrCode err = ApplyAttributes (sourceProjectPath, packagePath, processControl, reportLines, updateExisting);
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
            "embeddedLibraryFiles": { "type": "integer", "description": "The files of the project's embedded library copied next to the package (<name>-library)." },
            "favoritesExported": { "type": "boolean", "description": "The favorites were written next to the package (<name>-favorites.prf)." },
            "masterLayouts": { "type": "integer" },
            "layouts": { "type": "integer" },
            "layoutElements": { "type": "integer" },
            "layoutElementsSkipped": { "type": "integer" }
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
    CapturePenSetsAndProjectInfo (package, counts);
    {
        IO::Location embedded;
        counts.Add ("embeddedLibraryFiles", EmbeddedLibrary (embedded) ? CopyTree (embedded, IO::Location (LibraryPath (packagePath))) : 0);
    }
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
