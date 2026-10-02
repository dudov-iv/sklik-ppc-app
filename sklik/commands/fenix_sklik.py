"""Sklik campaign commands on the Fénix REST API (`fenix-*`): the authorized
user, campaigns, groups, ads and negative keywords.

Same engine and token as the Nákupy commands (`sklik/fenix.py`,
`SKLIK_FENIX_REFRESH_TOKEN`), but these endpoints (`/user/*`, `/sklik/*`) take
no `premiseId`. They do not replace the DRAK commands: Fénix (spec 1.30.0) has
no endpoints for positive keywords, keyword suggestions or search-query
reports. What it adds is `dryRun` validation of creates and — the one thing
DRAK cannot do at all — LISTING campaign-level negative keywords.

Written against the OpenAPI spec (v1.30.0). What a live account confirmed on
2026-10-02 and what is still spec-only (details in `docs/api-notes.md`):

* **verified live** — every read except `fenix-ads`: `fenix-me`,
  `fenix-credit`, `fenix-accounts`, `fenix-campaigns`, `fenix-groups`,
  `fenix-negatives --campaign-id` (campaign level); and the server-side dry run
  of `fenix-campaign-create` (a valid payload answers 2xx and persists nothing,
  an invalid one answers 422 naming the field).
* **NOT run live** — any real (non-dry) create, the dry run of
  `fenix-group-create` / `fenix-ad-create`, `fenix-negative-add`,
  `fenix-negative-remove`, `fenix-ads`, and `fenix-negatives --group-id`.

Money, per field:

* **haléře** — `budget.dayBudget` (campaigns) and `cpc` (groups): verified
  live, a 30 Kč budget reads `3000` and a 3 Kč bid reads `300`.
  `exhaustedDayBudget` and `cpm` are haléře per the spec (not seen live). The
  CLI takes CZK on the flags and converts; the human output converts back.
  `--json` passes the raw API payload through untouched (haléře), like the
  Nákupy commands do.
* **CZK** — `credit` / `creditWithVat` of `/user/me/credit`: verified live
  (same number as DRAK's `credit` command).
* **not stated** — `agencyLimit` of `/user/me/credit` ("Limit assigned by
  agency", an integer with no unit). Printed raw, never converted.

`devices.maxCpcMultiplier` is deliberately NOT requested: the spec words it
exactly like the Nákupy field of the same name ("multiplier in percent"), which
live data showed to be a signed modifier instead — showing it here would mean
guessing which of the two it is.
"""
from __future__ import annotations

import argparse
import json

import sklik.fenix as fenix
from sklik.api import _fail_msg
from sklik.formatting import _czk_to_halere, _format_money, _output_json

# The listings return only the attributes named in the `a` query parameter, and
# the spec does not say what comes back when it is omitted — so every listing
# asks for its columns explicitly. `isDeleted` rides along so the deleted-filter
# can be re-applied client-side (see `_live`).
_CAMPAIGN_ATTRS = ("id", "name", "type", "status", "isDeleted", "adSelection",
                   "paymentMethod", "createDate", "exhaustedDayBudget",
                   "budget.id", "budget.name", "budget.dayBudget")
_GROUP_ATTRS = ("id", "name", "status", "isDeleted", "createDate", "cpc", "cpm")
_AD_ATTRS = ("id", "type", "status", "adStatus", "isDeleted", "createDate",
             # ETA
             "headline1", "headline2", "headline3", "description", "description2",
             "path1", "path2", "finalUrl", "mobileFinalUrl", "trackingTemplate",
             # banner / combined naming, so non-ETA rows are not blank
             "name", "shortLine", "longLine", "companyName")
_NEGATIVE_ATTRS = ("name", "matchType")
_ACCOUNT_ATTRS = ("id", "username", "access", "agencyStatus", "relationStatus")

# Fénix calls the broad negative match plain `negative` (DRAK: `negativeBroad`).
# The DRAK spelling is accepted too, so both command families take the same value.
_MATCH_ALIASES = {"negativeBroad": "negative"}


def _s(value: object, default: str = "") -> str:
    """A table cell: a listing only carries the attributes it has a value for,
    and a null in a padded column would crash the f-string format spec."""
    return default if value is None else str(value)


def _live(items: list[dict]) -> list[dict]:
    """Drop deleted rows. `isDeleted=false` is sent as a query filter as well;
    this repeats it locally so a filter the API ignored cannot pass unnoticed
    (DRAK ignores most of its filters — same belt and braces as `shop-items`)."""
    return [i for i in items if not i.get("isDeleted")]


def _negatives_path(campaign_id: int, group_id: int | None) -> str:
    """Negative-keyword collection of a campaign, or of one of its groups."""
    base = f"/sklik/campaigns/{campaign_id}"
    if group_id:
        base += f"/groups/{group_id}"
    return f"{base}/negative-keywords/"


def _negative_item(args: argparse.Namespace) -> dict:
    return {"name": args.name,
            "matchType": _MATCH_ALIASES.get(args.match_type, args.match_type)}


def _created_ids(data: dict) -> list:
    return [i.get("id") for i in data.get("items") or [] if isinstance(i, dict)]


def _report_create(args: argparse.Namespace, label: str, key: str, data: dict,
                   **extra: object) -> None:
    """Shared output of the create commands (real create and server dry run).

    `extra` (e.g. the status a campaign was created with) is added to the JSON
    and shown in brackets in the human output."""
    if args.dry_run:
        # dryRun=true: the API validated the payload and persisted nothing. The
        # spec gives the dry-run answer no shape of its own (seen live for a
        # campaign: `{"items": []}`), so it is passed on as-is.
        if args.json:
            _output_json({"dryRun": True, "valid": True, "response": data})
        else:
            print(f"Dry run OK — {label.lower()} payload is valid; nothing was created.")
            if data:
                print(f"API response: {json.dumps(data, ensure_ascii=False)}")
        return
    ids = _created_ids(data)
    if args.json:
        _output_json({key: ids, **extra})
    else:
        note = ", ".join(f"{k}: {v}" for k, v in extra.items())
        print(f"{label} created: ID {ids[0] if ids else '?'}"
              + (f" ({note})" if note else ""))


def _local_dry_run(args: argparse.Namespace, method: str, path: str, body: dict) -> None:
    """`--dry-run` for the negative-keyword writes, which have NO `dryRun`
    parameter in the spec. Sending one would risk the API ignoring it and
    performing the write, so nothing is sent at all: the request is only shown.
    That makes it a preview, not a validation — and the output says so."""
    if args.json:
        _output_json({"dryRun": "local", "validated": False,
                      "wouldSend": {"method": method, "path": path, "body": body}})
    else:
        print(f"Dry run (local only — this endpoint has no server-side dryRun, "
              f"so nothing was validated or sent):\n  {method} {path}\n"
              f"  body: {json.dumps(body, ensure_ascii=False)}")


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

def cmd_fenix_me(args: argparse.Namespace) -> None:
    """Who the Fénix token acts as, and which scopes it carries."""
    d = fenix.call("GET", "/user/me")

    if args.json:
        _output_json(d)
        return
    print(f"User:    {d.get('userName', '—')} (ID {d.get('userId', '—')})")
    actor = d.get("actor")
    if actor:
        print(f"Actor:   {actor.get('userName', '—')} (ID {actor.get('userId', '—')}) "
              "— acting on the user's behalf")
    print(f"Scopes:  {', '.join(d.get('scope') or []) or '—'}")


def cmd_fenix_credit(args: argparse.Namespace) -> None:
    """Credit of the authorized user (CZK), or the agency limit of a client."""
    d = fenix.call("GET", "/user/me/credit")

    if args.json:
        _output_json(d)
        return
    print(f"Agency status:       {d.get('agencyStatus', '—')}")
    print(f"Verified in wallet:  {d.get('verifiedInWallet', '—')}")
    if "credit" in d:
        # Plain CZK per the spec — not haléře, do NOT use _format_money.
        print(f"Credit:              {d.get('credit')} Kč "
              f"(with VAT {d.get('creditWithVat')} Kč)")
    else:
        # Agency clients get `agencyLimit` instead of a credit. The spec gives
        # it no unit, so it is printed as-is rather than labelled Kč or haléře.
        print(f"Agency limit:        {_s(d.get('agencyLimit'), '—')} (unit not documented)")


def cmd_fenix_accounts(args: argparse.Namespace) -> None:
    """Accounts (own and managed) the token can access — IDs for --user-id."""
    data = fenix.call("GET", "/user/linked-accounts", {"a": list(_ACCOUNT_ATTRS)})
    accounts = data.get("items") or []

    if args.json:
        _output_json(accounts)
        return
    if not accounts:
        print("No accessible accounts.")
        return
    print(f"{'ID':<12} {'Username':<40} {'Access':<7} {'Relation':<9} {'Agency'}")
    print("-" * 86)
    for a in accounts:
        print(f"{_s(a.get('id')):<12} {_s(a.get('username')):<40} "
              f"{_s(a.get('access')):<7} {_s(a.get('relationStatus')):<9} "
              f"{a.get('agencyStatus') or '—'}")


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def cmd_fenix_campaigns(args: argparse.Namespace) -> None:
    """List campaigns."""
    params: dict = {"a": list(_CAMPAIGN_ATTRS), "isDeleted": "false",
                    "order": "id:ASC"}
    if args.status:
        params["status"] = args.status
    campaigns = _live(fenix.fetch_all_offset("/sklik/campaigns/", params))
    if args.status:
        campaigns = [c for c in campaigns if c.get("status") == args.status]

    if args.json:
        _output_json(campaigns)
        return
    if not campaigns:
        print("No campaigns found.")
        return
    print(f"{'ID':<12} {'Name':<35} {'Status':<10} {'Type':<10} "
          f"{'Day Budget':<12} {'Rotation':<10}")
    print("-" * 96)
    for c in campaigns:
        budget = c.get("budget") or {}
        # budget.dayBudget is haléře (spec: `BudgetResponse`).
        print(f"{_s(c.get('id')):<12} {_s(c.get('name')):<35} "
              f"{_s(c.get('status')):<10} {_s(c.get('type')):<10} "
              f"{_format_money(budget.get('dayBudget')):<12} "
              f"{c.get('adSelection') or '—':<10}")


def cmd_fenix_groups(args: argparse.Namespace) -> None:
    """List the ad groups of one campaign."""
    groups = _live(fenix.fetch_all_offset(
        f"/sklik/campaigns/{args.campaign_id}/groups/",
        {"a": list(_GROUP_ATTRS), "isDeleted": "false", "order": "id:ASC"}))

    if args.json:
        _output_json(groups)
        return
    if not groups:
        print("No groups found.")
        return
    print(f"{'ID':<12} {'Name':<35} {'Status':<10} {'Max CPC':<12} {'Max CPM'}")
    print("-" * 84)
    for g in groups:
        # cpc / cpm are haléře (spec: "Max CPC in hellers").
        print(f"{_s(g.get('id')):<12} {_s(g.get('name')):<35} "
              f"{_s(g.get('status')):<10} {_format_money(g.get('cpc')):<12} "
              f"{_format_money(g.get('cpm'))}")


def cmd_fenix_ads(args: argparse.Namespace) -> None:
    """List the ads of one group."""
    ads = _live(fenix.fetch_all_offset(
        f"/sklik/campaigns/{args.campaign_id}/groups/{args.group_id}/ads/",
        {"a": list(_AD_ATTRS), "isDeleted": "false", "order": "id:ASC"}))

    if args.json:
        _output_json(ads)
        return
    if not ads:
        print("No ads found.")
        return
    for a in ads:
        print(f"[{a.get('id')}] {_s(a.get('type'), '?')}  "
              f"status={_s(a.get('status'), '—')}  approval={_s(a.get('adStatus'), '—')}")
        if a.get("type") == "eta":
            heads = [a.get(k) for k in ("headline1", "headline2", "headline3")]
            print(f"    {' | '.join(h for h in heads if h)}")
            descs = [a.get(k) for k in ("description", "description2")]
            print(f"    {' '.join(d for d in descs if d)}")
        else:
            title = a.get("shortLine") or a.get("name")
            if title:
                print(f"    {title}")
        if a.get("finalUrl"):
            print(f"    → {a['finalUrl']}")


def cmd_fenix_negatives(args: argparse.Namespace) -> None:
    """List negative keywords of a campaign (campaign level) or of one group."""
    negatives = fenix.fetch_all_offset(
        _negatives_path(args.campaign_id, args.group_id),
        {"a": list(_NEGATIVE_ATTRS)})

    if args.json:
        _output_json(negatives)
        return
    level = (f"group {args.group_id}" if args.group_id
             else f"campaign {args.campaign_id} (campaign level)")
    if not negatives:
        print(f"No negative keywords on {level}.")
        return
    print(f"Negative keywords on {level}: {len(negatives)}\n")
    print(f"{'Keyword':<40} {'Match Type'}")
    print("-" * 56)
    for n in negatives:
        print(f"{_s(n.get('name')):<40} {_s(n.get('matchType'))}")


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def cmd_fenix_campaign_create(args: argparse.Namespace) -> None:
    """Create a campaign — paused unless --status active is passed."""
    # `suspend` is the paused value of the spec's Status enum (active|suspend).
    # A campaign created by a script should never start spending by default.
    status = args.status or "suspend"
    campaign: dict = {
        "type": args.type,
        "name": args.name,
        "budget": {"dayBudget": _czk_to_halere(args.day_budget)},  # haléře
        "status": status,
    }
    if args.ad_selection:
        campaign["adSelection"] = args.ad_selection

    data = fenix.call("POST", "/sklik/campaigns/",
                      {"dryRun": "true"} if args.dry_run else None, body=campaign)
    _report_create(args, "Campaign", "campaignIds", data, status=status)


def cmd_fenix_group_create(args: argparse.Namespace) -> None:
    """Create an ad group in a campaign."""
    if args.cpc is None and args.cpm is None:
        # The spec requires only `name`, and does not say what bid a group gets
        # without one. A CLI rule, not an API one: never leave a bid to an
        # undocumented server default.
        _fail_msg("--cpc or --cpm is required. The API itself accepts a group "
                  "without a bid, but the spec does not say what bid it then gets.")
    group: dict = {"name": args.name}
    if args.cpc is not None:
        group["cpc"] = _czk_to_halere(args.cpc)   # haléře
    if args.cpm is not None:
        group["cpm"] = _czk_to_halere(args.cpm)   # haléře
    if args.status:
        group["status"] = args.status

    data = fenix.call("POST", f"/sklik/campaigns/{args.campaign_id}/groups/",
                      {"dryRun": "true"} if args.dry_run else None, body=group)
    _report_create(args, "Group", "groupIds", data)


def cmd_fenix_ad_create(args: argparse.Namespace) -> None:
    """Create an ETA ad in a group."""
    ad: dict = {
        "type": "eta",
        "headline1": args.headline1,
        "headline2": args.headline2,
        "description": args.description1,
        "finalUrl": args.final_url,
    }
    for key, value in (("headline3", args.headline3),
                       ("description2", args.description2),
                       ("path1", args.path1), ("path2", args.path2),
                       ("mobileFinalUrl", args.mobile_final_url),
                       ("trackingTemplate", args.tracking_template),
                       ("status", args.status)):
        if value:
            ad[key] = value

    data = fenix.call(
        "POST", f"/sklik/campaigns/{args.campaign_id}/groups/{args.group_id}/ads/",
        {"dryRun": "true"} if args.dry_run else None, body=ad)
    _report_create(args, "Ad", "adIds", data)


def cmd_fenix_negative_add(args: argparse.Namespace) -> None:
    """Add a negative keyword at campaign level, or to one group."""
    path = _negatives_path(args.campaign_id, args.group_id)
    item = _negative_item(args)
    body = {"negativeKeywords": [item]}
    if args.dry_run:
        _local_dry_run(args, "POST", path, body)
        return

    fenix.call("POST", path, body=body)   # 201, no body
    level = "group" if args.group_id else "campaign"
    if args.json:
        _output_json({"added": 1, "level": level, "campaignId": args.campaign_id,
                      "groupId": args.group_id, "negativeKeyword": item})
    else:
        print(f"Negative keyword added ({level} level): "
              f"{item['name']} [{item['matchType']}]")


def cmd_fenix_negative_remove(args: argparse.Namespace) -> None:
    """Permanently delete a negative keyword (matched by text + match type)."""
    # Negative keywords have no IDs in Fénix — the delete names them by
    # name + matchType, in the request body of a POST to `…/delete`.
    path = _negatives_path(args.campaign_id, args.group_id) + "delete"
    item = _negative_item(args)
    body = {"negativeKeywords": [item]}
    if args.dry_run:
        _local_dry_run(args, "POST", path, body)
        return
    if not args.confirm:
        _fail_msg("Requires --confirm flag.")

    fenix.call("POST", path, body=body)   # 204, no body
    level = "group" if args.group_id else "campaign"
    if args.json:
        _output_json({"removed": True, "level": level,
                      "campaignId": args.campaign_id, "groupId": args.group_id,
                      "negativeKeyword": item})
    else:
        print(f"Negative keyword removed ({level} level): "
              f"{item['name']} [{item['matchType']}]")
