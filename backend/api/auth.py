from fastapi import APIRouter, HTTPException, Request
from backend.db import get_connection
from backend.auth import hash_password, verify_password
from backend.models.schemas import SignupRequest, LoginRequest, UserResponse

router = APIRouter()


@router.post("/auth/signup", response_model=UserResponse)
def signup(request: Request, body: SignupRequest):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE email = ?", (body.email,))
    if cursor.fetchone() is not None:
        conn.close()
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    password_hash, salt = hash_password(body.password)
    cursor.execute(
        "INSERT INTO users (email, password_hash, salt) VALUES (?, ?, ?)",
        (body.email, password_hash, salt),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()

    request.session["user_id"] = user_id
    return UserResponse(id=user_id, email=body.email)


@router.post("/auth/login", response_model=UserResponse)
def login(request: Request, body: LoginRequest):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE email = ?", (body.email,))
    user = cursor.fetchone()
    conn.close()

    if user is None or not verify_password(body.password, user["password_hash"], user["salt"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    request.session["user_id"] = user["id"]
    return UserResponse(id=user["id"], email=user["email"])


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "logged out"}


@router.get("/auth/me")
def me(request: Request):
    user_id = request.session.get("user_id")
    if user_id is None:
        return {"logged_in": False}

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, email FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()

    if user is None:
        request.session.clear()
        return {"logged_in": False}

    return {"logged_in": True, "id": user["id"], "email": user["email"]}
