from datetime import timedelta
import logging
import re
from urllib.parse import quote

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from absulli import __version__
from absulli.core.config import get_settings
from absulli.core.time import utcnow
from absulli.database.models import AbsUser, ActivitySession, Library, ListeningHistory, MediaItem, NotificationEvent
from absulli.database.session import get_db
from absulli.http.abs_client import AudiobookshelfClient
from absulli.monitors.scheduler import AbsulliScheduler
from absulli.core.security import (
    clear_csrf_cookie,
    clear_login_failures,
    clear_session_cookie,
    clamp_image_dimension,
    clean_image_format,
    auth_username,
    create_csrf_token,
    is_login_limited,
    login_retry_after_seconds,
    record_login_failure,
    record_login_event,
    set_csrf_cookie,
    set_session_cookie,
    password_hash,
    setup_required,
    validate_csrf_token,
    verify_login,
)
from absulli.core.setup_state import set_setup_settings
from absulli.web.queries import (
    active_sessions_query,
    enrich_active_rows,
    fmt_seconds,
    clamp_days,
    clean_stat_metric,
    clean_recent_type,
    clamp_recent_limit,
    build_home_cards,
    build_library_cards,
    media_history_date,
    media_window_stats,
    media_user_stats,
    resolve_media_title,
    library_icon,
    library_items,
    library_history_rows,
    library_window_stats,
    library_user_stats,
    library_recently_played_items,
    library_top_items,
    author_items,
    author_history_rows,
    author_window_stats,
    author_user_stats,
    user_display_name,
    user_initial,
    resolve_user,
    user_history_rows,
    user_window_stats,
    user_player_stats,
    user_library_stats,
    user_top_items,
    user_recently_played_items,
    author_cover_item_id,
    first_author_id_from_books,
    author_payload_value,
    clean_author_description,
    clean_graph_user,
    build_graphs,
)
from absulli.web.templating import templates

router = APIRouter()
log = logging.getLogger(__name__)

COVER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_cover_id(value: str, label: str) -> str:
    value = (value or "").strip()
    if not COVER_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"Invalid {label} ID")
    return value


def media_item_exists(db: Session, item_id: str) -> bool:
    return (
        db.query(MediaItem.id)
        .filter(MediaItem.abs_item_id == item_id)
        .first()
        is not None
    )


def author_exists(db: Session, author_id: str) -> bool:
    return (
        db.query(MediaItem.id)
        .filter(MediaItem.author_id == author_id, MediaItem.author_id != "")
        .first()
        is not None
    )


def safe_next_url(value: str | None) -> str:
    value = (value or "/").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


@router.get("/favicon.ico")
def favicon():
    return Response(status_code=204)




@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if not setup_required():
        return RedirectResponse(url="/", status_code=303)
    settings = get_settings()
    csrf_token = create_csrf_token()
    response = templates.TemplateResponse(
        "setup.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "abs_url": settings.effective_abs_url if settings.abs_url_from_env else "",
            "abs_url_from_env": settings.abs_url_from_env,
            "abs_api_key_from_env": settings.abs_api_key_from_env,
            "admin_username": auth_username(),
            "admin_login_from_env": bool(settings.auth_password_from_env or settings.auth_password_hash_from_env),
            "csrf_token": csrf_token,
            "error": "",
            "page": "setup",
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/setup/test-connection")
async def setup_test_connection(request: Request):
    if not setup_required():
        return JSONResponse(
            {"ok": False, "message": "Setup is already complete."},
            status_code=409,
        )

    settings = get_settings()
    form = await request.form()
    csrf_token = str(form.get("csrf_token") or request.headers.get("X-CSRF-Token") or "")
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse(
            {"ok": False, "message": "Your setup form expired. Refresh the page and try again."},
            status_code=403,
        )

    abs_url = str(form.get("abs_url") or "").strip().rstrip("/")
    abs_api_key = str(form.get("abs_api_key") or "").strip()

    if settings.abs_url_from_env:
        abs_url = settings.effective_abs_url
    if settings.abs_api_key_from_env:
        abs_api_key = settings.effective_abs_api_key

    if not abs_url.startswith(("http://", "https://")):
        return JSONResponse(
            {"ok": False, "message": "Audiobookshelf URL must start with http:// or https://."},
            status_code=400,
        )
    if not abs_api_key or abs_api_key == "change_me":
        return JSONResponse(
            {"ok": False, "message": "Audiobookshelf API key is required."},
            status_code=400,
        )

    try:
        async with httpx.AsyncClient(
            timeout=min(max(int(settings.abs_request_timeout or 15), 3), 30),
            verify=settings.abs_verify_ssl,
            headers={"Authorization": f"Bearer {abs_api_key}"},
        ) as client:
            response = await client.get(f"{abs_url}/api/me")
            if response.status_code == 404:
                response = await client.get(f"{abs_url}/api/libraries")
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            message = "Audiobookshelf rejected the API key."
        elif exc.response.status_code == 404:
            message = "Audiobookshelf responded, but the API endpoint was not found. Check the URL."
        else:
            message = f"Audiobookshelf returned HTTP {exc.response.status_code}."
        return JSONResponse({"ok": False, "message": message}, status_code=400)
    except httpx.ConnectError:
        return JSONResponse(
            {"ok": False, "message": "Could not connect to Audiobookshelf. Check the URL and network."},
            status_code=400,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"ok": False, "message": "Connection to Audiobookshelf timed out."},
            status_code=400,
        )
    except httpx.RequestError:
        return JSONResponse(
            {"ok": False, "message": "Audiobookshelf connection failed. Check the URL and SSL setting."},
            status_code=400,
        )

    return {"ok": True, "message": "Connection successful."}


async def run_initial_setup_import() -> None:
    try:
        scheduler = AbsulliScheduler(get_settings())
        await scheduler.poll_history()
        await scheduler.poll_activity()
        log.info("Initial setup import completed")
    except Exception as exc:  
        log.warning("Initial setup import failed: %s", exc)


@router.post("/setup", response_class=HTMLResponse)
def setup_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    abs_url: str = Form(""),
    abs_api_key: str = Form(""),
    admin_username: str = Form("admin"),
    admin_password: str = Form(""),
    confirm_password: str = Form(""),
    csrf_token: str = Form(""),
):
    if not setup_required():
        return RedirectResponse(url="/", status_code=303)

    settings = get_settings()

    def render_error(message: str, status_code: int = 400):
        new_csrf_token = create_csrf_token()
        response = templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "abs_url": settings.effective_abs_url if settings.abs_url_from_env else (abs_url or "").strip(),
                "abs_url_from_env": settings.abs_url_from_env,
                "abs_api_key_from_env": settings.abs_api_key_from_env,
                "admin_username": (admin_username or auth_username() or "admin").strip() or "admin",
                "admin_login_from_env": bool(settings.auth_password_from_env or settings.auth_password_hash_from_env),
                "csrf_token": new_csrf_token,
                "error": message,
                "page": "setup",
            },
            status_code=status_code,
        )
        set_csrf_cookie(response, new_csrf_token)
        return response

    if not validate_csrf_token(request, csrf_token):
        return render_error("Your setup form expired. Please try again.", status_code=403)

    abs_url = (abs_url or settings.effective_abs_url or "").strip().rstrip("/")
    abs_api_key = (abs_api_key or "").strip()
    admin_username = (admin_username or auth_username() or "admin").strip() or "admin"

    if settings.abs_url_from_env:
        abs_url = settings.effective_abs_url
    if settings.abs_api_key_from_env:
        abs_api_key = settings.effective_abs_api_key

    admin_login_from_env = bool(settings.auth_password_from_env or settings.auth_password_hash_from_env)

    if not abs_url.startswith(("http://", "https://")):
        return render_error("Audiobookshelf URL must start with http:// or https://.")
    if not abs_api_key or abs_api_key == "change_me":
        return render_error("Audiobookshelf API key is required.")
    if not admin_login_from_env:
        if len(admin_password or "") < 8:
            return render_error("Admin password must be at least 8 characters.")
        if admin_password != confirm_password:
            return render_error("Admin passwords do not match.")

    values = {"setup_complete": "true"}
    if not settings.abs_url_from_env:
        values["abs_url"] = abs_url
    if not settings.abs_api_key_from_env:
        values["abs_api_key"] = abs_api_key
    if not settings.auth_username_from_env:
        values["auth_username"] = admin_username
    if not admin_login_from_env:
        values["auth_password_hash"] = password_hash(admin_password)

    set_setup_settings(values)

    background_tasks.add_task(run_initial_setup_import)

    response = RedirectResponse(url="/", status_code=303)
    set_session_cookie(response, admin_username)
    rotated_csrf_token = create_csrf_token()
    set_csrf_cookie(response, rotated_csrf_token)
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    settings = get_settings()
    if setup_required():
        return RedirectResponse(url="/setup", status_code=303)
    if not settings.auth_enabled:
        return RedirectResponse(url="/", status_code=303)

    csrf_token = create_csrf_token()
    response = templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "next": safe_next_url(next),
            "csrf_token": csrf_token,
            "error": "",
            "page": "login",
        },
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    csrf_token: str = Form(""),
):
    settings = get_settings()
    target = safe_next_url(next)

    if not validate_csrf_token(request, csrf_token):
        record_login_event(request, username=username, success=False, reason="csrf_failed")
        new_csrf_token = create_csrf_token()
        response = templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "next": target,
                "csrf_token": new_csrf_token,
                "error": "Your login form expired. Please try again.",
                "page": "login",
            },
            status_code=403,
        )
        set_csrf_cookie(response, new_csrf_token)
        return response

    if is_login_limited(request):
        record_login_event(request, username=username, success=False, reason="rate_limited")
        retry_after = login_retry_after_seconds(request)
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "next": target,
                "csrf_token": csrf_token,
                "error": "Too many failed login attempts. Try again later.",
                "page": "login",
            },
            status_code=429,
            headers={"Retry-After": str(retry_after or 1)},
        )

    if verify_login(username, password):
        clear_login_failures(request)
        record_login_event(request, username=username, success=True, reason="success")
        response = RedirectResponse(url=target, status_code=303)
        set_session_cookie(response, auth_username())
        rotated_csrf_token = create_csrf_token()
        set_csrf_cookie(response, rotated_csrf_token)
        return response

    record_login_failure(request)
    record_login_event(request, username=username, success=False, reason="invalid_credentials")
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "next": target,
            "csrf_token": csrf_token,
            "error": "Invalid username or password.",
            "page": "login",
        },
        status_code=401,
    )


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    clear_csrf_cookie(response)
    return response




@router.get("/covers/items/{item_id}")
async def item_cover(
    item_id: str,
    width: int = 300,
    height: int | None = None,
    fmt: str = "webp",
    db: Session = Depends(get_db),
):
    item_id = validate_cover_id(item_id, "item")
    if not media_item_exists(db, item_id):
        raise HTTPException(status_code=404, detail="Cover not found")

    width = clamp_image_dimension(width, default=300) or 300
    height = clamp_image_dimension(height)
    fmt = clean_image_format(fmt)
    settings = get_settings()
    try:
        async with AudiobookshelfClient(settings) as client:
            content, content_type, cache_control = await client.get_item_cover(
                item_id=item_id,
                width=width,
                height=height,
                image_format=fmt,
            )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Cover not found") from exc
        raise HTTPException(status_code=502, detail="Audiobookshelf cover request failed") from exc

    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": cache_control},
    )


@router.get("/covers/authors/by-name/{author_name:path}")
async def author_cover_by_name(
    author_name: str,
    width: int = 420,
    height: int | None = None,
    fmt: str = "webp",
    db: Session = Depends(get_db),
):
    author_name = " ".join((author_name or "").split())
    if not author_name:
        raise HTTPException(status_code=404, detail="Author image not found")

    width = clamp_image_dimension(width, default=420) or 420
    height = clamp_image_dimension(height)
    fmt = clean_image_format(fmt)
    settings = get_settings()

    try:
        async with AudiobookshelfClient(settings) as client:
            library_ids = [row[0] for row in db.query(Library.abs_library_id).all()]
            author_payload = await client.find_author_in_libraries(author_name, library_ids)
            author_id = author_payload_value(author_payload, "id", "authorId", "_id", "asin")
            if not author_id:
                raise HTTPException(status_code=404, detail="Author image not found")
            content, content_type, cache_control = await client.get_author_image(
                author_id=author_id,
                width=width,
                height=height,
                image_format=fmt,
            )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Author image not found") from exc
        raise HTTPException(status_code=502, detail="Audiobookshelf author image request failed") from exc

    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": cache_control},
    )


@router.get("/covers/authors/{author_id}")
async def author_cover(
    author_id: str,
    width: int = 420,
    height: int | None = None,
    fmt: str = "webp",
    db: Session = Depends(get_db),
):
    author_id = validate_cover_id(author_id, "author")
    width = clamp_image_dimension(width, default=420) or 420
    height = clamp_image_dimension(height)
    fmt = clean_image_format(fmt)
    settings = get_settings()
    try:
        async with AudiobookshelfClient(settings) as client:
            content, content_type, cache_control = await client.get_author_image(
                author_id=author_id,
                width=width,
                height=height,
                image_format=fmt,
            )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Author image not found") from exc
        raise HTTPException(status_code=502, detail="Audiobookshelf author image request failed") from exc

    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": cache_control},
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    stat_metric = clean_stat_metric(request.query_params.get("metric"))
    stat_days = clamp_days(request.query_params.get("days"), default=30)
    recent_type = clean_recent_type(request.query_params.get("recent_type"))
    recent_limit = clamp_recent_limit(request.query_params.get("recent_limit"), default=50)
    active = active_sessions_query(db).order_by(desc(ActivitySession.last_seen_at)).all()
    enrich_active_rows(db, active)
    since = utcnow() - timedelta(days=stat_days)
    history_count = db.query(ListeningHistory).filter(ListeningHistory.imported_at >= since).count()
    total_seconds = db.query(func.coalesce(func.sum(ListeningHistory.duration_seconds), 0)).scalar() or 0
    users = db.query(AbsUser).count()
    recent = db.query(ListeningHistory).order_by(desc(ListeningHistory.imported_at)).limit(10).all()
    recent_items_query = db.query(MediaItem)
    if recent_type != "all":
        recent_items_query = recent_items_query.filter(MediaItem.media_type == recent_type)
    recent_items = (
        recent_items_query
        .order_by(desc(MediaItem.added_at), desc(MediaItem.updated_at))
        .limit(recent_limit)
        .all()
    )
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active": active,
            "history_count": history_count,
            "total_time": fmt_seconds(total_seconds),
            "users": users,
            "recent": recent,
            "recent_items": recent_items,
            "stat_cards": build_home_cards(db, stat_metric, stat_days),
            "library_cards": build_library_cards(db),
            "stat_metric": stat_metric,
            "stat_days": stat_days,
            "recent_type": recent_type,
            "recent_limit": recent_limit,
            "page": "dashboard",
        },
    )


@router.get("/activity", response_class=HTMLResponse)
def activity(request: Request, db: Session = Depends(get_db)):
    rows = active_sessions_query(db).order_by(desc(ActivitySession.last_seen_at)).limit(100).all()
    enrich_active_rows(db, rows)
    return templates.TemplateResponse("activity.html", {"request": request, "rows": rows, "page": "activity"})


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, db: Session = Depends(get_db)):
    rows = db.query(ListeningHistory).order_by(desc(media_history_date())).limit(250).all()
    return templates.TemplateResponse("history.html", {"request": request, "rows": rows, "page": "history", "fmt_seconds": fmt_seconds})



@router.get("/libraries", response_class=HTMLResponse)
def libraries(request: Request, db: Session = Depends(get_db)):
    rows = []
    for library in db.query(Library).order_by(Library.display_order.asc(), Library.name.asc()).all():
        imported_count = db.query(MediaItem).filter(MediaItem.library_id == library.abs_library_id).count()
        item_count = max(int(library.item_count or 0), int(imported_count or 0))
        recent_play = (
            db.query(func.max(media_history_date()))
            .filter(ListeningHistory.library_id == library.abs_library_id)
            .scalar()
        )
        rows.append(
            {
                "library": library,
                "icon": library_icon(library.media_type),
                "item_count": item_count,
                "recent_play": recent_play,
                "url": f"/libraries/{quote(library.abs_library_id, safe='')}",
            }
        )

    return templates.TemplateResponse(
        "libraries.html",
        {
            "request": request,
            "page": "libraries",
            "libraries": rows,
            "fmt_seconds": fmt_seconds,
        },
    )


@router.get("/libraries/{library_id}", response_class=HTMLResponse)
def library_detail(library_id: str, request: Request, db: Session = Depends(get_db)):
    library = db.query(Library).filter(Library.abs_library_id == library_id).first()
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")

    items = library_items(db, library.abs_library_id, limit=60)
    rows = library_history_rows(db, library, limit=250)
    item_count = max(int(library.item_count or 0), db.query(MediaItem).filter(MediaItem.library_id == library.abs_library_id).count())

    return templates.TemplateResponse(
        "library_detail.html",
        {
            "request": request,
            "page": "dashboard",
            "library": library,
            "library_icon": library_icon(library.media_type),
            "item_count": item_count,
            "items": items,
            "rows": rows,
            "recently_played": library_recently_played_items(db, library),
            "top_items": library_top_items(db, library),
            "window_stats": library_window_stats(db, library),
            "user_stats": library_user_stats(db, library),
            "fmt_seconds": fmt_seconds,
        },
    )


@router.get("/authors/{author_name:path}", response_class=HTMLResponse)
async def author_detail(author_name: str, request: Request, db: Session = Depends(get_db)):
    author_name = author_name.strip()
    if not author_name:
        raise HTTPException(status_code=404, detail="Author not found")

    books = author_items(db, author_name)
    rows = author_history_rows(db, author_name, limit=250)
    if not books and not rows:
        raise HTTPException(status_code=404, detail="Author not found")

    settings = get_settings()
    author_payload = None
    author_id = first_author_id_from_books(books)
    try:
        async with AudiobookshelfClient(settings) as client:
            if author_id:
                author_payload = await client.get_author(author_id)
            if not author_payload:
                library_ids = [row[0] for row in db.query(Library.abs_library_id).all()]
                author_payload = await client.find_author_in_libraries(author_name, library_ids)
                author_id = author_payload_value(author_payload, "id", "authorId", "_id", "asin") or author_id
            if author_id and (not author_payload or not author_payload_value(author_payload, "description", "desc", "bio", "biography", "summary")):
                author_payload = await client.get_author(author_id) or author_payload
    except Exception:  
        author_payload = None

    description = clean_author_description(
        author_payload_value(author_payload, "description", "desc", "bio", "biography", "summary")
    )

    return templates.TemplateResponse(
        "author_detail.html",
        {
            "request": request,
            "page": "history",
            "author": author_name,
            "author_id": author_id,
            "author_cover_url": f"/covers/authors/by-name/{quote(author_name, safe='')}",
            "author_description": description,
            "books": books,
            "rows": rows,
            "cover_item_id": author_cover_item_id(db, author_name),
            "window_stats": author_window_stats(db, author_name),
            "user_stats": author_user_stats(db, author_name),
            "fmt_seconds": fmt_seconds,
        },
    )

@router.get("/media/{item_id}", response_class=HTMLResponse)
def media_detail(item_id: str, request: Request, db: Session = Depends(get_db)):
    media_item = db.query(MediaItem).filter(MediaItem.abs_item_id == item_id).first()
    latest_history = (
        db.query(ListeningHistory)
        .filter(ListeningHistory.abs_item_id == item_id)
        .order_by(desc(ListeningHistory.imported_at))
        .first()
    )
    if not media_item and not latest_history:
        raise HTTPException(status_code=404, detail="Media item not found")

    rows = (
        db.query(ListeningHistory)
        .filter(ListeningHistory.abs_item_id == item_id)
        .order_by(desc(media_history_date()))
        .limit(250)
        .all()
    )
    title = resolve_media_title(media_item, latest_history, item_id)
    author = (media_item.author if media_item else "") or (latest_history.author if latest_history else "")
    media_type = (media_item.media_type if media_item else "") or (latest_history.media_type if latest_history else "unknown")
    library_name = (media_item.library_name if media_item else "") or (latest_history.library_name if latest_history else "")
    duration = (media_item.duration if media_item else 0) or 0

    return templates.TemplateResponse(
        "media_detail.html",
        {
            "request": request,
            "page": "history",
            "item_id": item_id,
            "title": title,
            "author": author,
            "author_url": f"/authors/{quote(author, safe='')}" if author else "",
            "media_type": media_type,
            "library_name": library_name,
            "duration": duration,
            "media_item": media_item,
            "rows": rows,
            "window_stats": media_window_stats(db, item_id),
            "user_stats": media_user_stats(db, item_id),
            "fmt_seconds": fmt_seconds,
        },
    )


@router.get("/users", response_class=HTMLResponse)
def users(request: Request, db: Session = Depends(get_db)):
    rows = db.query(AbsUser).order_by(AbsUser.username).all()
    return templates.TemplateResponse("users.html", {"request": request, "rows": rows, "page": "users"})


@router.get("/users/{user_key:path}", response_class=HTMLResponse)
def user_detail(user_key: str, request: Request, db: Session = Depends(get_db)):
    user_key = user_key.strip()
    user = resolve_user(db, user_key)
    rows = user_history_rows(db, user, user_key, limit=250)
    if not user and not rows:
        raise HTTPException(status_code=404, detail="User not found")

    display_name = user_display_name(user, user_key)
    return templates.TemplateResponse(
        "user_detail.html",
        {
            "request": request,
            "page": "users",
            "user": user,
            "user_key": user_key,
            "display_name": display_name,
            "initial": user_initial(display_name),
            "rows": rows,
            "window_stats": user_window_stats(db, user, user_key),
            "player_stats": user_player_stats(db, user, user_key),
            "library_stats": user_library_stats(db, user, user_key),
            "top_items": user_top_items(db, user, user_key),
            "recently_played": user_recently_played_items(db, user, user_key),
            "fmt_seconds": fmt_seconds,
        },
    )

@router.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", db: Session = Depends(get_db)):
    query = " ".join((q or "")[:200].split())
    like = f"%{query}%"
    media_items = []
    users = []
    libraries = []
    authors = []
    history_rows = []

    if query:
        media_items = (
            db.query(MediaItem)
            .filter(
                or_(
                    MediaItem.title.ilike(like),
                    MediaItem.author.ilike(like),
                    MediaItem.series.ilike(like),
                    MediaItem.library_name.ilike(like),
                )
            )
            .order_by(MediaItem.title.asc())
            .limit(30)
            .all()
        )
        users = (
            db.query(AbsUser)
            .filter(or_(AbsUser.username.ilike(like), AbsUser.display_name.ilike(like)))
            .order_by(AbsUser.username.asc())
            .limit(20)
            .all()
        )
        libraries = (
            db.query(Library)
            .filter(or_(Library.name.ilike(like), Library.media_type.ilike(like)))
            .order_by(Library.display_order.asc(), Library.name.asc())
            .limit(20)
            .all()
        )
        authors = (
            db.query(MediaItem.author, func.count(MediaItem.id).label("items"))
            .filter(MediaItem.author.ilike(like), MediaItem.author != "")
            .group_by(MediaItem.author)
            .order_by(desc("items"), MediaItem.author.asc())
            .limit(20)
            .all()
        )
        history_rows = (
            db.query(ListeningHistory)
            .filter(
                or_(
                    ListeningHistory.title.ilike(like),
                    ListeningHistory.author.ilike(like),
                    ListeningHistory.username.ilike(like),
                    ListeningHistory.library_name.ilike(like),
                    ListeningHistory.client.ilike(like),
                    ListeningHistory.device.ilike(like),
                    ListeningHistory.device_name.ilike(like),
                    ListeningHistory.model.ilike(like),
                )
            )
            .order_by(desc(media_history_date()))
            .limit(50)
            .all()
        )

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "page": "search",
            "search_query": query,
            "media_items": media_items,
            "users": users,
            "libraries": libraries,
            "authors": authors,
            "history_rows": history_rows,
            "fmt_seconds": fmt_seconds,
        },
    )




@router.get("/graphs", response_class=HTMLResponse)
def graphs(request: Request, db: Session = Depends(get_db)):
    stat_metric = clean_stat_metric(request.query_params.get("metric"))
    stat_days = clamp_days(request.query_params.get("days"), default=30)
    selected_user = clean_graph_user(request.query_params.get("user"))
    users = db.query(AbsUser).order_by(AbsUser.username.asc()).all()
    graph_data = build_graphs(db, stat_metric, stat_days, selected_user)
    return templates.TemplateResponse(
        "graphs.html",
        {
            "request": request,
            "page": "graphs",
            "stat_metric": stat_metric,
            "stat_days": stat_days,
            "selected_user": selected_user,
            "users": users,
            "graph_data": graph_data,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    settings = get_settings()
    safe = {
        "ABS_URL": settings.effective_abs_url,
        "ABS_POLL_INTERVAL": settings.abs_poll_interval,
        "ABS_HISTORY_POLL_INTERVAL": settings.abs_history_poll_interval,
        "GOTIFY_ENABLED": bool(settings.gotify_url and settings.gotify_token),
        "WEBHOOK_ENABLED": bool(settings.webhook_url),
    }
    return templates.TemplateResponse("settings.html", {"request": request, "settings": safe, "page": "settings", "app_version": __version__})


@router.get("/notifications", response_class=HTMLResponse)
def notifications(request: Request, db: Session = Depends(get_db)):
    rows = db.query(NotificationEvent).order_by(desc(NotificationEvent.created_at)).limit(200).all()
    return templates.TemplateResponse("notifications.html", {"request": request, "rows": rows, "page": "notifications"})
