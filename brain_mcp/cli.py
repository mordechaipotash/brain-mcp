#!/usr/bin/env python3
"""brain-mcp — CLI for brain-mcp."""

import argparse
import json
import sys
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "brain-mcp"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "brain.yaml"


def discover_sources():
    """Auto-discover conversation sources on this machine."""
    from rich.console import Console
    console = Console()
    sources = []

    checks = [
        ("Claude Code", "claude-code", Path.home() / ".claude" / "projects", "jsonl"),
        ("Clawdbot", "clawdbot", Path.home() / ".clawdbot" / "agents", "jsonl"),
    ]

    console.print("\n[bold]Discovering AI conversations...[/bold]\n")

    for name, source_type, path, fmt in checks:
        if path.exists():
            # Count files
            if fmt == "jsonl":
                files = list(path.rglob("*.jsonl"))
                count = len(files)
            else:
                count = 1

            if count > 0:
                console.print(f"   [green]found[/green] {name:<16} {count:>6} sessions    {path}")
                sources.append({
                    "type": source_type,
                    "path": str(path),
                    "name": name,
                })
            else:
                console.print(f"   [dim]--  {name:<16} empty           {path}[/dim]")
        else:
            console.print(f"   [dim]--  {name:<16} not found[/dim]")

    # Check for ChatGPT exports in Downloads
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        chatgpt_files = list(downloads.glob("chatgpt*.json")) + list(downloads.glob("conversations.json"))
        # Also check subdirectories one level deep
        for d in downloads.iterdir():
            if d.is_dir() and "chatgpt" in d.name.lower():
                chatgpt_files.extend(d.glob("conversations.json"))

        if chatgpt_files:
            console.print(f"   [green]found[/green] {'ChatGPT':<16} {len(chatgpt_files):>6} exports     {chatgpt_files[0].parent}")
            sources.append({
                "type": "chatgpt",
                "path": str(chatgpt_files[0].parent),
                "name": "ChatGPT",
            })
        else:
            console.print(f"   [dim]--  {'ChatGPT':<16} no exports in ~/Downloads[/dim]")

    # Check Cursor
    cursor_path = Path.home() / ".cursor"
    if cursor_path.exists():
        console.print(f"   [yellow]!![/yellow]  {'Cursor':<16} found but ingester not yet supported")

    # Check Windsurf
    windsurf_path = Path.home() / ".windsurf"
    if windsurf_path.exists():
        console.print(f"   [yellow]!![/yellow]  {'Windsurf':<16} found but ingester not yet supported")

    console.print()
    return sources


def create_config(sources, config_dir=None):
    """Create brain.yaml from discovered sources."""
    import yaml

    config_dir = config_dir or DEFAULT_CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)

    data_dir = config_dir / "data"
    vectors_dir = config_dir / "vectors"
    data_dir.mkdir(exist_ok=True)
    vectors_dir.mkdir(exist_ok=True)

    config = {
        "data_dir": str(data_dir),
        "vectors_dir": str(vectors_dir),
        "sources": sources,
        "embedding": {
            "model": "nomic-ai/nomic-embed-text-v1.5",
            "dim": 768,
            "batch_size": 50,
            "max_chars": 8000,
        },
        "summarizer": {
            "enabled": False,
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "api_key_env": "ANTHROPIC_API_KEY",
        },
        "domains": [
            "ai-dev", "backend-dev", "frontend-dev", "data-engineering",
            "devops", "database", "python", "web-scraping", "automation",
            "prompt-engineering", "documentation", "business-strategy",
            "career", "finance", "personal", "health", "education",
        ],
    }

    config_path = config_dir / "brain.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    return config_path


def cmd_init(args):
    """Discover sources and create config."""
    from rich.console import Console
    console = Console()

    sources = discover_sources()

    if not sources:
        console.print("[yellow]No conversation sources found.[/yellow]")
        console.print("You can manually add sources to brain.yaml later.")
        console.print("Supported: Claude Code JSONL, ChatGPT export, Clawdbot, generic JSONL")

    config_path = create_config(sources)
    total = sum(1 for s in sources)
    console.print(f"[bold green]Config saved to {config_path}[/bold green]")
    console.print(f"Found {total} source(s).\n")

    if args.full and sources:
        cmd_ingest(args)
        cmd_embed(args)
        console.print("\n[bold green]Your brain is ready![/bold green]\n")
        console.print("Next steps:")
        console.print("   brain-mcp setup claude          Connect to Claude Desktop + Code")
        console.print("   brain-mcp setup claude-desktop   Claude Desktop only")
        console.print("   brain-mcp setup claude-code      Claude Code only")
        console.print("   brain-mcp setup cursor           Connect to Cursor")
        console.print("   brain-mcp setup windsurf         Connect to Windsurf")
        console.print("   brain-mcp serve                  Start MCP server manually")
    elif sources:
        console.print("Next steps:")
        console.print("   brain-mcp init --full     Import everything now")
        console.print("   brain-mcp ingest          Import without embedding")


def cmd_ingest(args):
    """Import conversations from configured sources."""
    from rich.console import Console
    console = Console()

    from brain_mcp.config import load_config, set_config, get_config
    config_path = getattr(args, 'config', None) or DEFAULT_CONFIG_PATH
    if config_path and Path(config_path).exists():
        set_config(load_config(str(config_path)))

    cfg = get_config()
    console.print("\n[bold]Importing conversations...[/bold]\n")

    # Run ingest pipeline
    from brain_mcp.ingest import run_all_ingesters
    total = run_all_ingesters(cfg)

    console.print(f"\n[green]Total: {total:,} messages imported[/green]\n")


def cmd_embed(args):
    """Create/update vector embeddings."""
    from rich.console import Console
    console = Console()

    from brain_mcp.config import load_config, set_config, get_config
    config_path = getattr(args, 'config', None) or DEFAULT_CONFIG_PATH
    if config_path and Path(config_path).exists():
        set_config(load_config(str(config_path)))

    console.print("\n[bold]Creating embeddings...[/bold]\n")

    from brain_mcp.embed.embed import embed_messages
    embed_messages()

    console.print("\n[green]Embedding complete.[/green]\n")


def cmd_serve(args):
    """Start the MCP server."""
    from brain_mcp.config import load_config, set_config
    config_path = getattr(args, 'config', None) or DEFAULT_CONFIG_PATH
    if config_path and Path(config_path).exists():
        set_config(load_config(str(config_path)))

    from brain_mcp.server.server import create_server
    mcp = create_server()
    mcp.run()


def _write_mcp_config(config_file, console, label=None):
    """Write brain-mcp entry to an MCP config file."""
    import shutil

    if config_file.exists():
        with open(config_file) as f:
            config = json.load(f)
    else:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config = {}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    if "brain" in config["mcpServers"]:
        console.print(f"[yellow]Brain MCP already configured in {config_file}[/yellow]")
        return

    # Use full path to brain-mcp so the client can find it
    brain_cmd = shutil.which("brain-mcp") or "brain-mcp"

    config["mcpServers"]["brain"] = {
        "command": brain_cmd,
        "args": ["serve"],
    }

    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)

    display = label or config_file
    console.print(f"[green]✓ Brain MCP added to {display}[/green]  ({config_file})")


def cmd_setup(args):
    """Auto-configure an MCP client."""
    from rich.console import Console
    console = Console()

    client = args.client

    # Determine config file path
    import platform
    is_mac = platform.system() == "Darwin"

    if client in ("claude-desktop", "desktop"):
        if is_mac:
            config_file = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        else:
            config_file = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    elif client in ("claude-code", "code"):
        config_file = Path.home() / ".claude" / "mcp.json"
    elif client == "claude":
        # Auto-detect: set up BOTH if they exist, else whichever is found
        desktop = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json" if is_mac else Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
        code = Path.home() / ".claude" / "mcp.json"

        targets = []
        if desktop.parent.exists():
            targets.append(("Claude Desktop", desktop))
        if code.parent.exists():
            targets.append(("Claude Code", code))

        if not targets:
            # Neither installed — create Claude Code config (most common)
            targets.append(("Claude Code", code))

        for label, target_file in targets:
            _write_mcp_config(target_file, console, label)

        return
    elif client == "cursor":
        config_file = Path.home() / ".cursor" / "mcp.json"
    elif client == "windsurf":
        config_file = Path.home() / ".windsurf" / "mcp.json"
    else:
        console.print(f"[red]Unknown client: {client}[/red]")
        console.print("Supported: claude, claude-desktop, claude-code, cursor, windsurf")
        return

    _write_mcp_config(config_file, console)
    console.print(f"\nRestart {client.replace('-', ' ').title()} to activate.")


def cmd_doctor(args):
    """Health check."""
    from rich.console import Console
    console = Console()

    console.print("\n[bold]Brain MCP Health Check[/bold]\n")

    # Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    console.print(f"   [green]ok[/green] Python {py_ver}")

    # Config
    if DEFAULT_CONFIG_PATH.exists():
        console.print(f"   [green]ok[/green] Config: {DEFAULT_CONFIG_PATH}")
    else:
        console.print(f"   [red]missing[/red] Config: not found")
        console.print(f"      -> Run: brain-mcp init")
        return

    # Load config
    from brain_mcp.config import load_config, set_config, get_config
    set_config(load_config(str(DEFAULT_CONFIG_PATH)))
    cfg = get_config()

    # Parquet
    parquet_path = Path(cfg.data_dir) / "all_conversations.parquet"
    if parquet_path.exists():
        import duckdb
        con = duckdb.connect()
        count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{parquet_path}')").fetchone()[0]
        con.close()
        console.print(f"   [green]ok[/green] Parquet: {count:,} messages")
    else:
        console.print(f"   [red]missing[/red] Parquet: not found")
        console.print(f"      -> Run: brain-mcp ingest")

    # Vectors
    vectors_path = Path(cfg.vectors_dir) / "brain.lance"
    if vectors_path.exists():
        import lancedb
        db = lancedb.connect(str(Path(cfg.vectors_dir)))
        try:
            tbl = db.open_table("messages")
            vec_count = len(tbl)
            console.print(f"   [green]ok[/green] Vectors: {vec_count:,} embeddings")
        except Exception:
            console.print(f"   [yellow]warn[/yellow]  Vectors: directory exists but no table")
    else:
        console.print(f"   [yellow]warn[/yellow]  Vectors: not found")
        console.print(f"      -> Run: brain-mcp embed")

    # Summaries
    summaries_path = Path(cfg.data_dir) / "brain_summaries_v6.parquet"
    if summaries_path.exists():
        console.print(f"   [green]ok[/green] Summaries: available (prosthetic tools in full mode)")
    else:
        console.print(f"   [yellow]warn[/yellow]  Summaries: not generated (prosthetic tools in basic mode)")
        console.print(f"      -> Run: brain-mcp summarize (requires ANTHROPIC_API_KEY)")

    # MCP client configs
    import platform
    is_mac = platform.system() == "Darwin"
    desktop_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json" if is_mac else Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    for name, path in [
        ("Claude Desktop", desktop_path),
        ("Claude Code", Path.home() / ".claude" / "mcp.json"),
        ("Cursor", Path.home() / ".cursor" / "mcp.json"),
    ]:
        if path.exists():
            with open(path) as f:
                try:
                    cfg_json = json.load(f)
                    if "brain" in cfg_json.get("mcpServers", {}):
                        console.print(f"   [green]ok[/green] {name}: configured")
                    else:
                        console.print(f"   [dim]--  {name}: not configured[/dim]")
                        setup_name = name.lower().replace(" ", "-")
                        console.print(f"      -> Run: brain-mcp setup {setup_name}")
                except json.JSONDecodeError:
                    console.print(f"   [yellow]warn[/yellow]  {name}: config exists but invalid JSON")
        else:
            console.print(f"   [dim]--  {name}: not installed[/dim]")

    console.print()


def cmd_status(args):
    """Quick status one-liner."""
    from brain_mcp.config import load_config, set_config, get_config

    if not DEFAULT_CONFIG_PATH.exists():
        print("Brain not initialized. Run: brain-mcp init")
        return

    set_config(load_config(str(DEFAULT_CONFIG_PATH)))
    cfg = get_config()

    parquet_path = Path(cfg.data_dir) / "all_conversations.parquet"
    msg_count = 0
    if parquet_path.exists():
        import duckdb
        con = duckdb.connect()
        msg_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{parquet_path}')").fetchone()[0]
        con.close()

    vec_count = 0
    vectors_path = Path(cfg.vectors_dir)
    if vectors_path.exists():
        try:
            import lancedb
            db = lancedb.connect(str(vectors_path))
            tbl = db.open_table("messages")
            vec_count = len(tbl)
        except Exception:
            pass

    sources = len(cfg.sources) if cfg.sources else 0
    print(f"Brain: {msg_count:,} messages | {vec_count:,} vectors | {sources} sources")


def cmd_sync(args):
    """Incremental sync -- ingest new + embed new."""
    from rich.console import Console
    console = Console()

    console.print("[bold]Syncing...[/bold]")
    cmd_ingest(args)
    cmd_embed(args)
    console.print("[green]Sync complete[/green]")


def main():
    parser = argparse.ArgumentParser(
        prog="brain-mcp",
        description="Turn your AI conversations into a searchable second brain",
    )
    parser.add_argument("--config", help="Path to brain.yaml config file")

    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Discover sources and create config")
    p_init.add_argument("--full", action="store_true", help="Also ingest and embed")

    # ingest
    sub.add_parser("ingest", help="Import conversations from configured sources")

    # embed
    sub.add_parser("embed", help="Create/update vector embeddings")

    # serve
    sub.add_parser("serve", help="Start the MCP server")

    # setup
    p_setup = sub.add_parser("setup", help="Auto-configure an MCP client")
    p_setup.add_argument("client", choices=["claude", "claude-desktop", "desktop", "claude-code", "code", "cursor", "windsurf"])

    # doctor
    sub.add_parser("doctor", help="Health check")

    # status
    sub.add_parser("status", help="Quick status")

    # sync
    sub.add_parser("sync", help="Incremental update")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "ingest": cmd_ingest,
        "embed": cmd_embed,
        "serve": cmd_serve,
        "setup": cmd_setup,
        "doctor": cmd_doctor,
        "status": cmd_status,
        "sync": cmd_sync,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
