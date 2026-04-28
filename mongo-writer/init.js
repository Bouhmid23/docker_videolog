// MongoDB init — création des collections et index pour Videolog POC
db = db.getSiblingDB('videolog');

// Collection telemetry
db.createCollection('telemetry');
db.telemetry.createIndex({ room_name: 1, ts: 1 });
db.telemetry.createIndex({ ts: 1 });
db.telemetry.createIndex({ pts_ms: 1 });
db.telemetry.createIndex({ room_name: 1, pts_ms: 1 });

// Collection sessions
db.createCollection('sessions');
db.sessions.createIndex({ room_name: 1 }, { unique: true });
db.sessions.createIndex({ started_at: 1 });

print('✅ MongoDB videolog initialized with indexes');

// Collection room_state (état temps réel des rooms)
db.createCollection('room_state');
db.room_state.createIndex({ room_name: 1 }, { unique: true });
db.room_state.createIndex({ updated_at: 1 });

// Collection recordings (métadonnées des egress)
db.createCollection('recordings');
db.recordings.createIndex({ egress_id: 1 }, { unique: true });
db.recordings.createIndex({ room_name: 1, started_at: -1 });

print('✅ Collections room_state et recordings initialisées');

// Collection alerts
db.createCollection('alerts');
db.alerts.createIndex({ room_name: 1, ts: -1 });
db.alerts.createIndex({ type: 1, pts_ms: 1 });
db.alerts.createIndex({ inserted_at: 1 }, { expireAfterSeconds: 604800 });
print('✅ Collection alerts initialisée');
