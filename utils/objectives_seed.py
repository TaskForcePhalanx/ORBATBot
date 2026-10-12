"""The Objectives Library as first written — what a guild's library starts as.

Seeded once per guild into `cp_objective_library` (see
`database.ensure_objective_library()`); from then on the library is the
table, edited by admins on the Objectives page, and this file is only the
starting point. Generated from the *TFP Mission Objectives Library* doc.

Stdlib only, like `utils/campaign.py`.
"""

# (category, blurb, kind, entries). An objective entry is
# (name, players do, complete when, pairs with); a complication is
# (name, effect on play, INTSUM hint).

CATEGORIES = (
    ('Offensive',
     "The backbone of most ops. Players take ground or remove something from the enemy's order of battle.",
     'objective', (
        ('SEIZE', 'Take a piece of key terrain (hill, crossroads, bridge) and hold it briefly', 'Friendly forces control the area and no enemy are inside the marker', 'Counter-attack, Defend'),
        ('CLEAR', 'Move through a village, wood or compound and kill or drive out every enemy', 'No enemy left in the boundary (allow 2–3 stragglers)', 'Search for intel, Cache'),
        ('DESTROY', 'Eliminate a specific asset: SPAA, artillery battery, ammo dump, radar, HQ', 'Every listed asset is destroyed', 'Sabotage, Recon first'),
        ('ATTACK BY FIRE', 'Support element suppresses or destroys the enemy from a fixed position while others move', 'Enemy position suppressed until the assault element calls it complete', 'Clear, Breach'),
        ('BREACH', 'Get through a prepared obstacle: wire, minefield, anti-tank ditch, wall', 'A marked lane is open and the main body passes through', 'Attack by fire, Secure route'),
        ('ADVANCE TO CONTACT', 'Move along an axis until the enemy is found, then fight through', 'Unit reaches the limit of advance or the enemy main position is located', 'Recon, Delay (enemy)'),
        ('COUNTER-ATTACK', 'Retake ground the enemy has just captured', 'Objective back in friendly hands', 'Defend, Withdraw'),
        ('ASSAULT RIVER CROSSING', 'Get across a river under fire, using boats, a ford or a bridge', 'Bridgehead established on the far bank', 'Smoke, Seize bridge'),
    )),
    ('Defensive',
     'Good for a change of pace, or as the last phase after an attack. Give players 10–15 minutes to dig in before the first wave.',
     'objective', (
        ('DEFEND', 'Hold a position against waves of attackers', 'Position still held when the timer runs out, or after the final wave', 'Resupply, Casualty evacuation'),
        ('DELAY', 'Slow a larger enemy force, giving up ground in stages', 'Enemy held at each phase line for a set time, and the unit is still combat effective', 'Withdraw, Demolish bridge'),
        ('HOLD UNTIL RELIEVED', 'Keep a position until a friendly force arrives', 'Relief force reaches the position', 'Defend, Link-up'),
        ('WITHDRAW', 'Break contact and pull back to a rally point without losing people', 'Everyone at the rally point (allow a set number of casualties)', 'Smoke, Rear guard'),
        ('REAR GUARD', 'A small team holds a choke point while the main body withdraws', 'Main body clear and the rear guard extracted', 'Withdraw, Delay'),
        ('HASTY DEFENCE', 'Consolidate on a newly taken objective and beat off a counter-attack', 'Counter-attack defeated', 'Seize, Clear'),
    )),
    ('Reconnaissance and security',
     'Usually a secondary objective for a small team, or a phase 1 that feeds the rest of the op. It rewards stealth and good radio reports.',
     'objective', (
        ('ROUTE RECON', 'Check a road for obstacles, bridges, ambush sites and enemy', 'Route reported as passable or not, with each obstacle located', 'Secure route, Convoy'),
        ('AREA RECON', 'Find out what is in a village, wood or compound without being seen', 'Enemy strength and key positions reported', 'Destroy, Raid'),
        ('ZONE RECON', 'Search a larger area for enemy forces', 'Every enemy position in the zone reported', 'Advance to contact'),
        ('OBSERVATION POST', 'Set up hidden and watch an area for a set time', 'Required sightings reported (vehicle count, patrol times, HVT arrival)', 'Ambush, Raid'),
        ('SCREEN', 'Watch a flank and give early warning of enemy movement', 'Enemy approach reported before it reaches the main body', 'Defend, Delay'),
        ('LOCATE', 'Find something specific: a mobile launcher, a downed aircraft, a mortar firing on friendly forces', 'Object found and grid sent', 'Destroy, CSAR'),
        ('BATTLE DAMAGE ASSESSMENT', 'Go back to a struck target and confirm the damage', 'Damage confirmed by photo or report', 'Destroy, Intel'),
    )),
    ('Raids and special tasks',
     'Short, sharp and memorable. Players get in, do one thing, and get out. These work best with a clear exfil point and a ticking clock.',
     'objective', (
        ('CAPTURE HVT', 'Find a named officer, detain him and bring him back alive', 'HVT alive at the exfil point', 'Area recon, Exfil under fire'),
        ('KILL HVT', 'Eliminate a named target', 'HVT confirmed dead (body checked or photographed)', 'OP, Ambush'),
        ('HOSTAGE / POW RESCUE', 'Free friendly prisoners and get them out', 'A set number of hostages alive at the exfil point', 'Stealth, Casualty evacuation'),
        ('RECOVER INTEL', 'Grab documents, a laptop or a map case', 'Intel item picked up and carried to exfil', 'Clear, BDA'),
        ('SABOTAGE', 'Plant charges on a bridge, rail line, fuel depot or comms mast', 'Target destroyed by player-placed explosives', 'Infiltration, Withdraw'),
        ('CSAR', 'Find downed aircrew and bring them home', 'Aircrew alive at the pickup point', 'Locate, Air extraction'),
        ('STEAL / RECOVER EQUIPMENT', 'Take an enemy vehicle, weapon system or crypto gear intact', 'Item delivered to a friendly location', 'Raid, Convoy'),
        ('AMBUSH', 'Destroy an enemy column or patrol at a chosen killing area', 'Every vehicle in the column destroyed or disabled', 'OP, Withdraw'),
    )),
    ('Enabling and mobility',
     'Objectives that open the way for someone else. They suit mechanised ops and give drivers, engineers and logistics players a real job.',
     'objective', (
        ('SECURE ROUTE', 'Make a road safe for follow-on forces by clearing the threats along it', 'Every marked threat on the route cleared', 'Route recon, Convoy'),
        ('CONVOY / ESCORT', 'Get vehicles from A to B intact', 'A set number of vehicles arrive', 'IED, Ambush (enemy)'),
        ('ROUTE CLEARANCE', 'Find and deal with IEDs or mines on a route', 'Route declared clear with no vehicle losses', 'Secure route, VCP'),
        ('SEIZE BRIDGE', 'Take a bridge intact before the enemy blows it', 'Bridge alive and in friendly hands', 'River crossing, Defend'),
        ('DEMOLISH BRIDGE', 'Destroy a crossing to stop enemy movement', 'Bridge destroyed before the enemy column arrives', 'Delay, Rear guard'),
        ('ESTABLISH FOB / PATROL BASE', 'Build a defended position with supply, respawn and a medical area', 'Respawn and supply point placed inside a secured perimeter', 'Defend, Resupply'),
        ('LINK-UP', 'Meet a friendly force at a set point without a friendly-fire incident', 'Both units at the link-up point, with recognition signals exchanged', 'Hold until relieved, Air assault'),
        ('FORWARD PASSAGE OF LINES', 'Pass one unit through another that is already in contact', 'Passing unit through and the stationary unit relieved', 'Clear, Advance to contact'),
    )),
    ('Stability and asymmetric',
     'For counter-insurgency, peacekeeping or occupation campaigns. The challenge is judgement as much as shooting, so set clear rules of engagement in the OPORD.',
     'objective', (
        ('CORDON AND SEARCH', 'Seal off a village, then search it house by house', 'Every marked building searched and suspects detained', 'Cache, Key leader engagement'),
        ('FIND WEAPONS CACHE', 'Locate and destroy a hidden arms cache', 'Cache found and destroyed', 'Recover intel, Demolish'),
        ('VEHICLE CHECKPOINT (VCP)', 'Stop and search traffic on a road', 'A set number of vehicles checked, and the wanted vehicle or person found', 'ROE pressure, Route clearance'),
        ('KEY LEADER ENGAGEMENT', 'Meet a local leader, protect him, and get information or agreement', 'Meeting held and the leader unharmed', 'Escort, Cordon and search'),
        ('ESCORT VIP', 'Move a civilian, journalist or officer safely through hostile ground', 'VIP alive at the destination', 'Convoy, Secure route'),
        ('PRESENCE PATROL', 'Patrol a route, showing the flag and reacting to incidents', 'Route completed, with each incident handled correctly', 'QRF, VCP'),
        ('QUICK REACTION FORCE', 'Respond to a call for help somewhere else on the map', 'Friendly unit relieved, or the incident resolved within a time limit', 'Presence patrol, Casualty evacuation'),
        ('HUMANITARIAN AID', 'Deliver supplies or protect a distribution point', 'Supplies delivered and civilians unharmed', 'Convoy, Defend'),
    )),
    ('Air and combined arms',
     'Use these when the ORBAT includes pilots, crews or a JTAC. Each one gives the specialist slot a task the infantry depends on.',
     'objective', (
        ('AIR ASSAULT', 'Insert by helicopter onto or near the objective', 'Assault force on the ground at the LZ with no aircraft lost', 'Seize, LZ security'),
        ('SECURE LZ / PZ', 'Clear and hold a landing zone for incoming aircraft', 'Aircraft lands and lifts off without taking fire', 'Air assault, CSAR'),
        ('SUPPRESS ENEMY AIR DEFENCES', 'Find and destroy SAM or SPAA sites before air support can be used', 'Every listed AA asset destroyed', 'Destroy, Recon'),
        ('CALL FOR FIRE / CAS', 'A JTAC or forward observer directs artillery or aircraft onto targets', 'Each target destroyed by supporting fire, with no friendly-fire incidents', 'OP, Defend'),
        ('CASUALTY EVACUATION', 'Move casualties to an aid station or pickup point', 'A set number of casualties delivered alive', 'Defend, QRF'),
        ('ARMOUR SUPPORT', 'Infantry and vehicles move and fight together', 'Objective taken with fewer than a set number of vehicles lost', 'Clear, Advance to contact'),
        ('AIR EXTRACTION', 'Get the force out by helicopter from a hot area', 'Every surviving player aboard and clear', 'Withdraw, CSAR'),
    )),
    ('Logistics and sustainment',
     'Rarely a primary objective, but they make a long op feel real. They also give the platoon sergeant, logistics Zeus and drivers something to own.',
     'objective', (
        ('RESUPPLY FORWARD UNIT', 'Get ammunition, medical supplies or fuel to a unit in contact', 'Supplies delivered before the unit runs dry', 'Defend, Convoy'),
        ('VEHICLE RECOVERY', 'Recover a disabled friendly vehicle under fire', 'Vehicle towed or repaired and back at a friendly position', 'Secure route, Hasty defence'),
        ('ESTABLISH FARP', 'Set up a forward arming and refuelling point for helicopters', 'Fuel and ammo point working at a secured site', 'Air assault, FOB'),
        ('RECOVER THE FALLEN', 'Bring back casualties left behind earlier in the op', 'Bodies or body bags returned to base', 'Withdraw, Counter-attack'),
        ('PROTECT SUPPLY LINE', 'Stop enemy raids on a supply route between two points', 'Set number of supply runs complete without loss', 'Screen, QRF'),
    )),
    ('Complications and modifiers',
     'Layer one of these onto any objective. Hint at it in the INTSUM ("suspected", "unconfirmed") so players have a fair chance to plan for it.',
     'complication', (
        ('Armoured counter-attack', 'Players must consolidate fast and use their AT', '"Enemy armour reported in reserve to the NW"'),
        ('Enemy QRF timer', 'Raids become a race against the clock', '"Enemy QRF based at [town], response time 10–15 minutes"'),
        ('Indirect fire', 'Players must spread out and keep moving', '"Suspected 120mm mortars, not confirmed"'),
        ('IEDs and mines', 'Slows movement and tests route discipline', '"Enemy known to mine approaches to defended villages"'),
        ('Night or bad weather', 'Shorter engagement ranges, harder navigation', '"Fog expected after 2200"'),
        ('Comms degraded', 'Leaders must use runners, flares and pre-agreed signals', '"Enemy EW capability assessed as likely"'),
        ('Civilians present', 'Tight ROE; every shot needs positive identification', '"Civilian population still present in [town]"'),
        ('Limited ammunition', 'Resupply becomes a real objective', '"Second-line resupply not available until Phase 3"'),
        ('Moving target', 'HVT or convoy relocates if players are slow', '"HVT believed to move location frequently"'),
        ('Friendly force in the area', 'Fire control and recognition matter', '"Friendly recce elements operating forward of PL BERLIN"'),
        ('Enemy deception', 'What the intel said is wrong, so leaders must adapt', '"Intel confidence: low"'),
        ('Casualty-producing event', 'Medics and CASEVAC get a real workout', 'None, or "enemy known to use VBIEDs"'),
    )),
)
