// deslab: BuildModelFromScan - point cloud (E57) -> walls, doors, windows, floor slabs, zones.
// See ScanToBimCommands.hpp and scan2bim/README.md for the scene format.

#include "ScanToBimCommands.hpp"
#include "ElementCommands.hpp"
#include "ElementCreationCommands.hpp"
#include "ExtendedElementCommands.hpp"
#include "MigrationHelper.hpp"

#include "File.hpp"
#include "FileSystem.hpp"
#include "ObjectStateJSONConversion.hpp"
#include "Process.hpp"

namespace {

constexpr const char* SceneFormat = "deslab.scan2bim/1";

GS::UniString GetString (const GS::ObjectState& os, const char* field, const GS::UniString& defaultValue = GS::EmptyUniString)
{
    GS::UniString value;
    return os.Get (field, value) ? value : defaultValue;
}

double GetNumber (const GS::ObjectState& os, const char* field, double defaultValue)
{
    double value = defaultValue;
    os.Get (field, value);
    return value;
}

bool GetFlag (const GS::ObjectState& os, const char* field, bool defaultValue)
{
    bool value = defaultValue;
    os.Get (field, value);
    return value;
}

GSErrCode LoadJsonFile (const GS::UniString& path, GS::ObjectState& os)
{
    const IO::Location location (path);
    IO::File file (location);
    GSErrCode err = file.Open (IO::File::OpenMode::ReadMode);
    if (err != NoError) {
        return err;
    }
    err = JSON::ConvertToObjectState (file, os);
    file.Close ();
    return err;
}

// The helper ships next to the add-on: <add-on folder>/deslab-scan2bim/deslab-scan2bim.exe.
GS::UniString DefaultHelperPath ()
{
    IO::Location location;
    ACAPI_GetOwnLocation (&location);
    location.DeleteLastLocalName ();
    location.AppendToLocal (IO::Name ("deslab-scan2bim"));
#ifdef WINDOWS
    location.AppendToLocal (IO::Name ("deslab-scan2bim.exe"));
#else
    location.AppendToLocal (IO::Name ("deslab-scan2bim"));
#endif
    GS::UniString path;
    location.ToPath (&path);
    return path;
}

GS::Optional<GS::UniString> RunHelper (const GS::UniString& helperPath, const GS::UniString& e57Path, const GS::UniString& scenePath, const GS::ObjectState& parameters)
{
    GS::Array<GS::UniString> arguments = { e57Path, "-o", scenePath };
    double value = 0.0;
    if (parameters.Get ("exteriorThickness", value)) {
        arguments.Push ("--exterior-thickness");
        arguments.Push (GS::ValueToUniString (value));
    }
    if (parameters.Get ("defaultThickness", value)) {
        arguments.Push ("--default-thickness");
        arguments.Push (GS::ValueToUniString (value));
    }
    Int32 step = 0;
    if (parameters.Get ("step", step) && step > 0) {
        arguments.Push ("--step");
        arguments.Push (GS::ValueToUniString (step));
    }
    try {
        GS::Process process = GS::Process::Create (helperPath, arguments, GS::Process::CreateNoWindow, true, false, true);
        if (!process.IsValid ()) {
            return GS::UniString ("Failed to start the point cloud helper: ") + helperPath;
        }
        process.WaitFor ();
        // A failed analysis writes {"error": ...} into the scene file; that message is shown later.
    } catch (...) {
        return GS::UniString ("Failed to run the point cloud helper: ") + helperPath;
    }
    return GS::NoValue;
}

GS::ObjectState Coordinate (const GS::ObjectState& point, double dx, double dy)
{
    GS::ObjectState c;
    c.Add ("x", GetNumber (point, "x", 0.0) + dx);
    c.Add ("y", GetNumber (point, "y", 0.0) + dy);
    return c;
}

// The created element of every input item, or the error of the item, in input order.
GS::Array<GS::ObjectState> ResultItems (const GS::ObjectState& response)
{
    GS::Array<GS::ObjectState> items;
    response.Get ("elements", items);
    return items;
}

bool GetCreatedGuid (const GS::ObjectState& item, API_Guid& guid)
{
    const GS::ObjectState* elementId = item.Get ("elementId");
    if (elementId == nullptr) {
        return false;
    }
    guid = GetGuidFromObjectState (*elementId);
    return guid != APINULLGuid;
}

GS::UniString ErrorMessage (const GS::ObjectState& item)
{
    const GS::ObjectState* error = item.Get ("error");
    if (error == nullptr) {
        error = &item;
    }
    return GetString (*error, "message", "unknown error");
}

// Attribute (layer, building material) by its name; empty when there is none.
GS::Optional<API_Attr_Head> FindAttribute (API_AttrTypeID typeID, const GS::UniString& name)
{
    if (name.IsEmpty ()) {
        return GS::NoValue;
    }
    GS::UniString nameCopy = name;
    API_Attr_Head head = {};
    head.typeID = typeID;
    head.uniStringNamePtr = &nameCopy;
    if (ACAPI_Attribute_Search (&head) != NoError) {
        return GS::NoValue;
    }
    API_Attribute attr = {};
    attr.header.typeID = typeID;
    attr.header.index = head.index;
    if (ACAPI_Attribute_Get (&attr) != NoError) {
        return GS::NoValue;
    }
    head.guid = attr.header.guid;
    head.uniStringNamePtr = nullptr;
    return head;
}

} // namespace

BuildModelFromScanCommand::BuildModelFromScanCommand () :
    CommandBase (CommonSchema::NotUsed)
{
}

GS::String BuildModelFromScanCommand::GetName () const
{
    return "BuildModelFromScan";
}

GS::Optional<GS::UniString> BuildModelFromScanCommand::GetInputParametersSchema () const
{
    return R"({
        "type": "object",
        "properties": {
            "e57Path": {
                "type": "string",
                "description": "Point cloud to analyse. The deslab-scan2bim helper turns it into a scene JSON first (next to the E57 as <name>.scene.json unless scenePath is given)."
            },
            "scenePath": {
                "type": "string",
                "description": "Scene JSON to build from (format deslab.scan2bim/1). With e57Path it is where the helper writes the scene."
            },
            "scene": {
                "type": "object",
                "description": "The scene itself instead of a file."
            },
            "helperPath": {
                "type": "string",
                "description": "Optional path of deslab-scan2bim.exe. Default: the deslab-scan2bim folder next to the add-on file."
            },
            "step": { "type": "integer", "minimum": 1, "description": "Helper: keep every n-th point (default 4)." },
            "exteriorThickness": { "type": "number", "exclusiveMinimum": 0, "description": "Helper: thickness of a one-sided wall with a window (default 0.64)." },
            "defaultThickness": { "type": "number", "exclusiveMinimum": 0, "description": "Helper: thickness of other one-sided walls (default 0.38)." },
            "floorIndex": { "type": "integer", "description": "Story to build on. Default 0." },
            "origin": {
                "type": "object",
                "description": "Where the scene origin goes in the project. Default (0, 0).",
                "properties": { "x": { "type": "number" }, "y": { "type": "number" } },
                "additionalProperties": false,
                "required": ["x", "y"]
            },
            "favorites": {
                "type": "object",
                "description": "Favorite names to create the elements from (as GetFavoritesByType lists them).",
                "properties": {
                    "wall": { "type": "string" },
                    "door": { "type": "string" },
                    "window": { "type": "string" },
                    "slab": { "type": "string" },
                    "zone": { "type": "string" }
                },
                "additionalProperties": false
            },
            "layers": {
                "type": "object",
                "description": "Layer names to put the elements on after creation.",
                "properties": {
                    "walls": { "type": "string" },
                    "openings": { "type": "string" },
                    "slabs": { "type": "string" },
                    "zones": { "type": "string" }
                },
                "additionalProperties": false
            },
            "materials": {
                "type": "object",
                "description": "Building material names for the walls: measured (both sides scanned) and assumed (one side scanned, thickness by default).",
                "properties": {
                    "measuredWalls": { "type": "string" },
                    "assumedWalls": { "type": "string" },
                    "slabs": { "type": "string" }
                },
                "additionalProperties": false
            },
            "slabThickness": { "type": "number", "exclusiveMinimum": 0, "description": "Thickness of the floor slabs, their top at the room floor. Default 0.22." },
            "buildWalls": { "type": "boolean", "description": "Default true." },
            "buildOpenings": { "type": "boolean", "description": "Default true." },
            "buildSlabs": { "type": "boolean", "description": "Default true." },
            "buildZones": { "type": "boolean", "description": "Default true." }
        },
        "additionalProperties": false
    })";
}

GS::Optional<GS::UniString> BuildModelFromScanCommand::GetRawResponseSchema () const
{
    return R"({
        "type": "object",
        "properties": {
            "scenePath": { "type": "string" },
            "walls": { "type": "array", "items": { "type": "object" } },
            "doors": { "type": "array", "items": { "type": "object" } },
            "windows": { "type": "array", "items": { "type": "object" } },
            "slabs": { "type": "array", "items": { "type": "object" } },
            "zones": { "type": "array", "items": { "type": "object" } },
            "errors": { "type": "array", "items": { "type": "string" } },
            "assumptions": { "type": "array", "items": { "type": "string" } },
            "warnings": { "type": "array", "items": { "type": "string" } }
        },
        "additionalProperties": false,
        "required": ["walls", "doors", "windows", "slabs", "zones", "errors"]
    })";
}

GS::ObjectState BuildModelFromScanCommand::Execute (const GS::ObjectState& parameters, GS::ProcessControl& processControl) const
{
    // 1. The scene: inline, from a file, or made by the helper from the E57.
    GS::ObjectState scene;
    GS::UniString scenePath = GetString (parameters, "scenePath");
    const GS::UniString e57Path = GetString (parameters, "e57Path");
    if (parameters.Get ("scene", scene)) {
        scenePath = GS::EmptyUniString;
    } else {
        if (!e57Path.IsEmpty ()) {
            if (scenePath.IsEmpty ()) {
                scenePath = e57Path + ".scene.json";
            }
            const GS::UniString helperPath = GetString (parameters, "helperPath", DefaultHelperPath ());
            const auto helperError = RunHelper (helperPath, e57Path, scenePath, parameters);
            if (helperError.HasValue ()) {
                return CreateErrorResponse (APIERR_GENERAL, helperError.Get ());
            }
        }
        if (scenePath.IsEmpty ()) {
            return CreateErrorResponse (APIERR_BADPARS, "Give e57Path, scenePath or scene.");
        }
        if (LoadJsonFile (scenePath, scene) != NoError || scene.IsEmpty ()) {
            return CreateErrorResponse (APIERR_GENERAL, GS::UniString ("Failed to read the scene file ") + scenePath);
        }
    }
    GS::UniString sceneError;
    if (scene.Get ("error", sceneError)) {
        return CreateErrorResponse (APIERR_GENERAL, GS::UniString ("Point cloud analysis failed: ") + sceneError);
    }
    if (GetString (scene, "format") != SceneFormat) {
        return CreateErrorResponse (APIERR_BADPARS, GS::UniString ("The scene is not in the ") + SceneFormat + " format.");
    }

    // 2. Options.
    Int32 floorIndex = 0;
    parameters.Get ("floorIndex", floorIndex);
    double dx = 0.0;
    double dy = 0.0;
    if (const GS::ObjectState* origin = parameters.Get ("origin")) {
        dx = GetNumber (*origin, "x", 0.0);
        dy = GetNumber (*origin, "y", 0.0);
    }
    GS::ObjectState favorites;
    parameters.Get ("favorites", favorites);
    GS::ObjectState layers;
    parameters.Get ("layers", layers);
    GS::ObjectState materials;
    parameters.Get ("materials", materials);
    const double slabThickness = GetNumber (parameters, "slabThickness", 0.22);
    const Stories stories = GetStories ();

    GS::ObjectState response;
    if (!scenePath.IsEmpty ()) {
        response.Add ("scenePath", scenePath);
    }
    const auto& wallList = response.AddList<GS::ObjectState> ("walls");
    const auto& doorList = response.AddList<GS::ObjectState> ("doors");
    const auto& windowList = response.AddList<GS::ObjectState> ("windows");
    const auto& slabList = response.AddList<GS::ObjectState> ("slabs");
    const auto& zoneList = response.AddList<GS::ObjectState> ("zones");
    const auto& errorList = response.AddList<GS::UniString> ("errors");
    for (const char* field : { "assumptions", "warnings" }) {
        GS::Array<GS::UniString> texts;
        scene.Get (field, texts);
        const auto& list = response.AddList<GS::UniString> (field);
        for (const GS::UniString& text : texts) {
            list (text);
        }
    }

    GS::Array<GS::ObjectState> sceneWalls;
    scene.Get ("walls", sceneWalls);
    GS::Array<GS::ObjectState> sceneRooms;
    scene.Get ("rooms", sceneRooms);

    // Layers are set after the creation with SetDetailsOfElements, per element group.
    GS::Array<GS::Pair<API_Guid, GS::UniString>> layerJobs;
    auto layerFor = [&] (const API_Guid& guid, const char* group) {
        const GS::UniString name = GetString (layers, group);
        if (!name.IsEmpty ()) {
            layerJobs.Push (GS::Pair<API_Guid, GS::UniString> (guid, name));
        }
    };

    // 3. Walls, in scene order.
    GS::Array<API_Guid> wallGuids;
    for (UIndex i = 0; i < sceneWalls.GetSize (); ++i) {
        wallGuids.Push (APINULLGuid);
    }
    if (GetFlag (parameters, "buildWalls", true) && !sceneWalls.IsEmpty ()) {
        const auto measuredMaterial = FindAttribute (API_BuildingMaterialID, GetString (materials, "measuredWalls"));
        const auto assumedMaterial = FindAttribute (API_BuildingMaterialID, GetString (materials, "assumedWalls"));
        GS::ObjectState wallParameters;
        const auto& wallsData = wallParameters.AddList<GS::ObjectState> ("wallsData");
        for (const GS::ObjectState& wall : sceneWalls) {
            GS::ObjectState data;
            const GS::UniString favorite = GetString (favorites, "wall");
            if (!favorite.IsEmpty ()) {
                data.Add ("favoriteName", favorite);
            }
            const GS::ObjectState* start = wall.Get ("start");
            const GS::ObjectState* end = wall.Get ("end");
            if (start == nullptr || end == nullptr) {
                continue;
            }
            data.Add ("begCoordinate", Coordinate (*start, dx, dy));
            data.Add ("endCoordinate", Coordinate (*end, dx, dy));
            data.Add ("floorIndex", floorIndex);
            data.Add ("zCoordinate", GetNumber (wall, "bottom", 0.0));
            data.Add ("height", GetNumber (wall, "height", 2.7));
            data.Add ("thickness", GetNumber (wall, "thickness", 0.2));
            data.Add ("referenceLineLocation", "Center");
            const bool measured = GetFlag (wall, "measuredThickness", false);
            const auto& material = measured ? measuredMaterial : assumedMaterial;
            if (material.HasValue ()) {
                data.Add ("buildingMaterialId", CreateGuidObjectState (material.Get ().guid));
            } else {
                data.Add ("structureType", "Basic");
            }
            wallsData (data);
        }
        const GS::Array<GS::ObjectState> results = ResultItems (CreateWallsCommand ().Execute (wallParameters, processControl));
        for (UIndex i = 0; i < results.GetSize () && i < sceneWalls.GetSize (); ++i) {
            const GS::UniString id = GetString (sceneWalls[i], "id");
            API_Guid guid = APINULLGuid;
            if (GetCreatedGuid (results[i], guid)) {
                wallGuids[i] = guid;
                GS::ObjectState item ("id", id);
                item.Add ("elementId", CreateGuidObjectState (guid));
                wallList (item);
                layerFor (guid, "walls");
            } else {
                errorList (GS::UniString ("Wall ") + id + ": " + ErrorMessage (results[i]));
            }
        }
    }

    // 4. Doors and windows in their walls.
    if (GetFlag (parameters, "buildOpenings", true)) {
        GS::ObjectState doorParameters;
        const auto& doorsData = doorParameters.AddList<GS::ObjectState> ("doorsData");
        GS::ObjectState windowParameters;
        const auto& windowsData = windowParameters.AddList<GS::ObjectState> ("windowsData");
        GS::Array<GS::UniString> doorIds;
        GS::Array<GS::UniString> windowIds;
        for (UIndex i = 0; i < sceneWalls.GetSize (); ++i) {
            if (wallGuids[i] == APINULLGuid) {
                continue;
            }
            GS::Array<GS::ObjectState> openings;
            sceneWalls[i].Get ("openings", openings);
            for (const GS::ObjectState& opening : openings) {
                const bool isWindow = GetString (opening, "kind") == "window";
                GS::ObjectState data;
                GS::ObjectState wallId;
                wallId.Add ("guid", APIGuidToString (wallGuids[i]));
                data.Add ("ownerWallId", wallId);
                data.Add ("centerOffset", GetNumber (opening, "centerOffset", 0.0));
                data.Add ("width", GetNumber (opening, "width", 0.9));
                data.Add ("height", GetNumber (opening, "height", 2.1));
                data.Add ("sillHeight", GetNumber (opening, "sill", 0.0));
                const GS::UniString favorite = GetString (favorites, isWindow ? "window" : "door");
                if (!favorite.IsEmpty ()) {
                    data.Add ("favoriteName", favorite);
                }
                if (isWindow) {
                    windowsData (data);
                    windowIds.Push (GetString (opening, "id"));
                } else {
                    doorsData (data);
                    doorIds.Push (GetString (opening, "id"));
                }
            }
        }
        auto collect = [&] (const GS::ObjectState& response, const GS::Array<GS::UniString>& ids, const auto& list, const char* what) {
            const GS::Array<GS::ObjectState> results = ResultItems (response);
            for (UIndex i = 0; i < results.GetSize () && i < ids.GetSize (); ++i) {
                API_Guid guid = APINULLGuid;
                if (GetCreatedGuid (results[i], guid)) {
                    GS::ObjectState item ("id", ids[i]);
                    item.Add ("elementId", CreateGuidObjectState (guid));
                    list (item);
                    layerFor (guid, "openings");
                } else {
                    errorList (GS::UniString (what) + " " + ids[i] + ": " + ErrorMessage (results[i]));
                }
            }
        };
        if (!doorIds.IsEmpty ()) {
            collect (CreateDoorsCommand ().Execute (doorParameters, processControl), doorIds, doorList, "Door");
        }
        if (!windowIds.IsEmpty ()) {
            collect (CreateWindowsCommand ().Execute (windowParameters, processControl), windowIds, windowList, "Window");
        }
    }

    // 5. Floor slabs and zones per room.
    auto polygonOf = [&] (const GS::ObjectState& room) {
        GS::Array<GS::ObjectState> points;
        room.Get ("polygon", points);
        GS::Array<GS::ObjectState> coordinates;
        for (const GS::ObjectState& p : points) {
            coordinates.Push (Coordinate (p, dx, dy));
        }
        return coordinates;
    };
    if (GetFlag (parameters, "buildSlabs", true) && !sceneRooms.IsEmpty ()) {
        const auto slabMaterial = FindAttribute (API_BuildingMaterialID, GetString (materials, "slabs"));
        GS::ObjectState slabParameters;
        const auto& slabsData = slabParameters.AddList<GS::ObjectState> ("slabsData");
        for (const GS::ObjectState& room : sceneRooms) {
            GS::ObjectState data;
            const GS::UniString favorite = GetString (favorites, "slab");
            if (!favorite.IsEmpty ()) {
                data.Add ("favoriteName", favorite);
            }
            data.Add ("floorIndex", floorIndex);
            data.Add ("level", GetZPos (static_cast<short> (floorIndex), GetNumber (room, "floor", 0.0), stories));
            data.Add ("thickness", slabThickness);
            data.Add ("referencePlaneLocation", "Top");
            if (slabMaterial.HasValue ()) {
                data.Add ("buildingMaterialId", CreateGuidObjectState (slabMaterial.Get ().guid));
            } else {
                data.Add ("structureType", "Basic");
            }
            const auto& coordinates = data.AddList<GS::ObjectState> ("polygonCoordinates");
            for (const GS::ObjectState& c : polygonOf (room)) {
                coordinates (c);
            }
            slabsData (data);
        }
        const GS::Array<GS::ObjectState> results = ResultItems (CreateSlabsCommand ().Execute (slabParameters, processControl));
        for (UIndex i = 0; i < results.GetSize () && i < sceneRooms.GetSize (); ++i) {
            const GS::UniString id = GetString (sceneRooms[i], "id");
            API_Guid guid = APINULLGuid;
            if (GetCreatedGuid (results[i], guid)) {
                GS::ObjectState item ("id", id);
                item.Add ("elementId", CreateGuidObjectState (guid));
                slabList (item);
                layerFor (guid, "slabs");
            } else {
                errorList (GS::UniString ("Slab ") + id + ": " + ErrorMessage (results[i]));
            }
        }
    }
    if (GetFlag (parameters, "buildZones", true) && !sceneRooms.IsEmpty ()) {
        GS::ObjectState zoneParameters;
        const auto& zonesData = zoneParameters.AddList<GS::ObjectState> ("zonesData");
        for (const GS::ObjectState& room : sceneRooms) {
            GS::ObjectState data;
            const GS::UniString favorite = GetString (favorites, "zone");
            if (!favorite.IsEmpty ()) {
                data.Add ("favoriteName", favorite);
            }
            data.Add ("floorIndex", floorIndex);
            data.Add ("name", GetString (room, "name", "Room"));
            Int32 number = 0;
            room.Get ("number", number);
            data.Add ("numberStr", GS::ValueToUniString (number));
            GS::ObjectState geometry;
            const auto& coordinates = geometry.AddList<GS::ObjectState> ("polygonCoordinates");
            for (const GS::ObjectState& c : polygonOf (room)) {
                coordinates (c);
            }
            data.Add ("geometry", geometry);
            zonesData (data);
        }
        const GS::Array<GS::ObjectState> results = ResultItems (CreateZonesCommand ().Execute (zoneParameters, processControl));
        for (UIndex i = 0; i < results.GetSize () && i < sceneRooms.GetSize (); ++i) {
            const GS::UniString id = GetString (sceneRooms[i], "id");
            API_Guid guid = APINULLGuid;
            if (GetCreatedGuid (results[i], guid)) {
                GS::ObjectState item ("id", id);
                item.Add ("elementId", CreateGuidObjectState (guid));
                zoneList (item);
                layerFor (guid, "zones");
            } else {
                errorList (GS::UniString ("Zone ") + id + ": " + ErrorMessage (results[i]));
            }
        }
    }

    // 6. Layers.
    if (!layerJobs.IsEmpty ()) {
        GS::ObjectState detailParameters;
        const auto& elementsWithDetails = detailParameters.AddList<GS::ObjectState> ("elementsWithDetails");
        GS::HashTable<GS::UniString, Int32> layerIndices;
        for (const auto& job : layerJobs) {
            if (!layerIndices.ContainsKey (job.second)) {
                const auto layer = FindAttribute (API_LayerID, job.second);
                layerIndices.Add (job.second, layer.HasValue () ? GetAttributeIndex (layer.Get ().index) : -1);
                if (!layer.HasValue ()) {
                    errorList (GS::UniString ("Layer not found: ") + job.second);
                }
            }
            const Int32 layerIndex = layerIndices[job.second];
            if (layerIndex < 0) {
                continue;
            }
            GS::ObjectState details;
            details.Add ("layerIndex", layerIndex);
            GS::ObjectState item;
            item.Add ("elementId", CreateGuidObjectState (job.first));
            item.Add ("details", details);
            elementsWithDetails (item);
        }
        SetDetailsOfElementsCommand ().Execute (detailParameters, processControl);
    }

    return response;
}
