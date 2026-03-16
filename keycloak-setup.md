# Keycloak Setup for SvcMgr

After starting `docker-compose up`, configure Keycloak:

## 1. Access Keycloak Admin Console

- URL: http://localhost:8080/admin
- Login: `admin` / `admin`

## 2. Create Realm

1. Click dropdown (top-left, "master") -> **Create Realm**
2. Realm name: `svcmgr`
3. Click **Create**

## 3. Create Client

1. Go to **Clients** -> **Create client**
2. Client ID: `svcmgr-app`
3. Client authentication: **Off** (public client)
4. Click **Next**
5. Direct access grants: **On** (enables Resource Owner Password flow)
6. Valid redirect URIs: `http://localhost:5000/*`
7. Web origins: `http://localhost:5000`
8. Click **Save**

## 4. Create Users

1. Go to **Users** -> **Add user**
2. Username: `admin`
3. Email: `admin@example.com`
4. First name: `Admin`
5. Last name: `User`
6. Email verified: **On**
7. Click **Create**
8. Go to **Credentials** tab -> **Set password**
9. Password: `admin` (temporary: **Off**)

## 5. Create Roles (Optional)

1. Go to **Realm roles** -> **Create role**
2. Create roles: `admin`, `operator`, `viewer`
3. Assign roles to users via **Users** -> user -> **Role mapping**

## Environment Variables

For local development (`.env`):
```
KEYCLOAK_URL=http://localhost:8080
KEYCLOAK_REALM=svcmgr
KEYCLOAK_CLIENT_ID=svcmgr-app
KEYCLOAK_CLIENT_SECRET=
```

For Docker (set in `docker-compose.yml`):
```
KEYCLOAK_URL=http://keycloak:8080
```
