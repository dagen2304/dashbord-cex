# Graph Report - v2-dashboard  (2026-10-26)

## Corpus Check
- Corpus is ~20,156 words - fits in a single context window. You may not need a graph.

## Summary
- 682 nodes · 1153 edges · 61 communities (37 shown, 24 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 42 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Metrics & Collector Core
- Compose App Services
- Device Collectors
- Compose Data Services
- Application Endpoints
- Inventory Read API
- Reports & Analytics
- Frontend Dev Deps
- Device Discovery
- TS Config
- K8s Manifests
- InfluxDB Manager
- Frontend UI Deps
- Device Registration & Creds
- K8s ConfigMap
- Frontend Core Deps
- K8s Container Specs
- K8s Namespace & Ingress
- K8s Frontend/Backend Services
- K8s Worker & PVCs
- VM Diagnostic API
- WebSocket Broadcast
- Application Schemas
- K8s Frontend Deployment
- FastAPI Entrypoint
- API Router & DB Session
- TS Node Config
- K8s Storage Specs
- K8s Secrets Data
- Simulated Fallback State
- Vite Build
- K8s Ingress Rules
- NPM Scripts
- K8s Celery Beat
- K8s InfluxDB
- K8s Redis
- K8s Redis Service
- K8s Secret
- K8s Postgres Service
- K8s InfluxDB Service
- PNPM Workspace
- Env Injection Script
- Misc 43
- Misc 44
- Misc 45
- Misc 46
- Misc 47
- Misc 48
- Misc 49
- Misc 50

## God Nodes (most connected - your core abstractions)
1. `BaseCollector` - 22 edges
2. `InfrastructureDevice` - 19 edges
3. `name` - 19 edges
4. `compilerOptions` - 18 edges
5. `namespace` - 18 edges
6. `data` - 15 edges
7. `Base` - 14 edges
8. `selector` - 14 edges
9. `_generate_all_reports_data()` - 11 edges
10. `SystemSetting` - 11 edges

## Surprising Connections (you probably didn't know these)
- `create_application()` --uses--> `ApplicationCreate`  [INFERRED]
  backend/app/api/endpoints/app_endpoints.py → backend/app/schemas/application.py
- `update_application()` --uses--> `ApplicationCreate`  [INFERRED]
  backend/app/api/endpoints/app_endpoints.py → backend/app/schemas/application.py
- `get_inventory_devices()` --uses--> `InfrastructureDevice`  [INFERRED]
  backend/app/api/endpoints/inventory_endpoints.py → backend/app/models/inventory.py
- `register_new_device()` --uses--> `DeviceCreate`  [INFERRED]
  backend/app/api/endpoints/inventory_endpoints.py → backend/app/schemas/inventory.py
- `update_device()` --uses--> `InfrastructureDevice`  [INFERRED]
  backend/app/api/endpoints/inventory_endpoints.py → backend/app/models/inventory.py

## Import Cycles
- None detected.

## Communities (61 total, 24 thin omitted)

### Community 0 - "Metrics & Collector Core"
Cohesion: 0.06
Nodes (15): get_realtime_metrics(), send_latency_alert_email(), MetricHistory, BaseCollector, DataDomainCollector, DoradoCollector, NetBackupCollector, PowerStoreCollector (+7 more)

### Community 1 - "Compose App Services"
Cohesion: 0.06
Nodes (46): build, container_name, depends_on, deploy, environment, networks, ports, restart (+38 more)

### Community 3 - "Compose Data Services"
Cohesion: 0.07
Nodes (37): container_name, environment, healthcheck, image, networks, restart, volumes, POSTGRES_DB (+29 more)

### Community 4 - "Application Endpoints"
Cohesion: 0.12
Nodes (11): create_application(), datetime_seed_offset(), delete_application(), get_all_applications(), get_application_by_id(), get_application_metrics(), list_available_vms(), update_application() (+3 more)

### Community 5 - "Inventory Read API"
Cohesion: 0.11
Nodes (12): get_inventory_devices(), get_latency_anomalies(), get_metrics_explorer(), get_metrics_objects(), get_system_settings(), get_vm_latency(), LatencyAnomalyResponse, Config (+4 more)

### Community 6 - "Reports & Analytics"
Cohesion: 0.13
Nodes (5): export_capacity_csv(), export_capacity_excel(), _generate_all_reports_data(), get_capacity_analytics_data(), CapacityAnalyticsService

### Community 7 - "Frontend Dev Deps"
Cohesion: 0.09
Nodes (23): devDependencies, autoprefixer@^10.4.14, eslint@^8.45.0, eslint-plugin-react-hooks@^4.6.0, eslint-plugin-react-refresh@^0.4.3, postcss@^8.4.27, tailwindcss@^3.3.3, @types/react@18.2.15 (+15 more)

### Community 8 - "Device Discovery"
Cohesion: 0.14
Nodes (11): _check_port_open(), discover_device_signature(), DeviceBase, DeviceCreate, DeviceDiscoveryInput, DeviceDiscoveryResponse, DeviceResponse, DeviceUpdate (+3 more)

### Community 9 - "TS Config"
Cohesion: 0.10
Nodes (20): compilerOptions, allowImportingTsExtensions, allowSyntheticDefaultImports, esModuleInterop, isolatedModules, jsx, lib, module (+12 more)

### Community 10 - "K8s Manifests"
Cohesion: 0.12
Nodes (21): apiVersion, kind, metadata, spec, spec, spec, apiVersion, kind (+13 more)

### Community 12 - "Frontend UI Deps"
Cohesion: 0.11
Nodes (19): dependencies, axios@^1.4.0, html2canvas@^1.4.1, lodash@^4.17.21, lucide-react@^0.263.1, @radix-ui/react-dialog@^1.0.4, @radix-ui/react-dropdown-menu@^2.0.5, @radix-ui/react-toast@^1.1.4 (+11 more)

### Community 13 - "Device Registration & Creds"
Cohesion: 0.14
Nodes (4): delete_device(), register_new_device(), InfrastructureDevice, ServiceDiscovery

### Community 14 - "K8s ConfigMap"
Cohesion: 0.13
Nodes (15): data, DOCKER_INFLUXDB_INIT_BUCKET, DOCKER_INFLUXDB_INIT_MODE, DOCKER_INFLUXDB_INIT_ORG, DOCKER_INFLUXDB_INIT_RETENTION, DOCKER_INFLUXDB_INIT_USERNAME, FRONTEND_URL, INFLUXDB_BUCKET (+7 more)

### Community 15 - "Frontend Core Deps"
Cohesion: 0.15
Nodes (12): class-variance-authority@^0.7.0, clsx@^2.0.0, react@^18.2.0, tailwind-merge@^1.14.0, name, private, type, version (+4 more)

### Community 16 - "K8s Container Specs"
Cohesion: 0.15
Nodes (13): command, envFrom, image, imagePullPolicy, name, ports, readinessProbe, volumeMounts (+5 more)

### Community 17 - "K8s Namespace & Ingress"
Cohesion: 0.15
Nodes (13): Namespace noc, Ingress noc-ingress, PersistentVolumeClaim postgres-pvc, apiVersion, kind, metadata, name, apiVersion (+5 more)

### Community 18 - "K8s Frontend/Backend Services"
Cohesion: 0.15
Nodes (13): Service backend, Service frontend, port, targetPort, apiVersion, kind, metadata, spec (+5 more)

### Community 19 - "K8s Worker & PVCs"
Cohesion: 0.17
Nodes (11): apiVersion, kind, Deployment celery-worker, PersistentVolumeClaim influxdb-pvc, PersistentVolumeClaim redis-pvc, apiVersion, kind, metadata (+3 more)

### Community 20 - "VM Diagnostic API"
Cohesion: 0.20
Nodes (4): get_clusters_for_vcenter(), get_vm_filesystems(), get_vm_saturation(), get_vm_saturation_history()

### Community 21 - "WebSocket Broadcast"
Cohesion: 0.29
Nodes (3): ConnectionManager, redis_listener(), websocket_endpoint()

### Community 22 - "Application Schemas"
Cohesion: 0.27
Nodes (4): ApplicationBase, ApplicationCreate, ApplicationResponse, VMSchema

### Community 23 - "K8s Frontend Deployment"
Cohesion: 0.20
Nodes (10): apiVersion, kind, metadata, metadata, apiVersion, kind, metadata, Deployment frontend (+2 more)

### Community 24 - "FastAPI Entrypoint"
Cohesion: 0.28
Nodes (3): health_check(), root(), startup_db_setup()

### Community 26 - "TS Node Config"
Cohesion: 0.25
Nodes (7): compilerOptions, allowSyntheticDefaultImports, composite, module, moduleResolution, skipLibCheck, include

### Community 27 - "K8s Storage Specs"
Cohesion: 0.39
Nodes (8): spec, spec, spec, storage, requests, accessModes, resources, storageClassName

### Community 28 - "K8s Secrets Data"
Cohesion: 0.25
Nodes (8): stringData, DATABASE_URL, DOCKER_INFLUXDB_INIT_ADMIN_TOKEN, DOCKER_INFLUXDB_INIT_PASSWORD, INFLUXDB_TOKEN, INTERNAL_API_KEY, POSTGRES_PASSWORD, SECRET_KEY

### Community 29 - "Simulated Fallback State"
Cohesion: 0.47
Nodes (3): get_collector_fallback(), get_fallback_simulated_state(), fetch_state()

### Community 30 - "Vite Build"
Cohesion: 0.33
Nodes (4): vite@^4.4.5, @vitejs/plugin-react@^4.0.3, vite, @vitejs/plugin-react

### Community 31 - "K8s Ingress Rules"
Cohesion: 0.33
Nodes (6): paths, spec, host, http, ingressClassName, rules

### Community 32 - "NPM Scripts"
Cohesion: 0.40
Nodes (5): scripts, build, dev, lint, preview

### Community 33 - "K8s Celery Beat"
Cohesion: 0.40
Nodes (5): apiVersion, kind, metadata, spec, Deployment celery-beat

### Community 34 - "K8s InfluxDB"
Cohesion: 0.40
Nodes (5): apiVersion, kind, metadata, spec, Deployment influxdb

### Community 35 - "K8s Redis"
Cohesion: 0.40
Nodes (5): apiVersion, kind, metadata, spec, Deployment redis

### Community 36 - "K8s Redis Service"
Cohesion: 0.40
Nodes (5): Service redis, apiVersion, kind, metadata, spec

### Community 37 - "K8s Secret"
Cohesion: 0.40
Nodes (5): Secret noc-secrets, apiVersion, kind, metadata, type

### Community 38 - "K8s Postgres Service"
Cohesion: 0.40
Nodes (5): Service postgres, apiVersion, kind, metadata, spec

### Community 39 - "K8s InfluxDB Service"
Cohesion: 0.40
Nodes (5): Service influxdb, apiVersion, kind, metadata, spec

## Knowledge Gaps
- **24 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `InfluxDBManager` connect `InfluxDB Manager` to `Metrics & Collector Core`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `InfrastructureDevice` (e.g. with `delete_device()` and `get_inventory_devices()`) actually correct?**
  _`InfrastructureDevice` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Should `Metrics & Collector Core` be split into smaller, more focused modules?**
  _Cohesion score 0.06280701754385964 - nodes in this community are weakly interconnected._
- **Why does `InfrastructureDevice` connect `Device Registration & Creds` to `Metrics & Collector Core`, `Application Endpoints`, `Inventory Read API`, `Reports & Analytics`, `FastAPI Entrypoint`, `API Router & DB Session`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Should `Compose App Services` be split into smaller, more focused modules?**
  _Cohesion score 0.05507246376811594 - nodes in this community are weakly interconnected._
- **Why does `VeritasCollector` connect `Device Collectors` to `Metrics & Collector Core`?**
  _High betweenness centrality (0.015) - this node is a cross-community bridge._
- **Should `Device Collectors` be split into smaller, more focused modules?**
  _Cohesion score 0.0613107822410148 - nodes in this community are weakly interconnected._