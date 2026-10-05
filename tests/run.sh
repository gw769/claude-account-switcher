#!/usr/bin/env bash
# Tests for claude-account. Everything runs against fake Claude Desktop
# processes and a temporary config folder; the real ~/.config/Claude and the
# real app are never touched.
#
#   bash tests/run.sh

set -uo pipefail
unset -f grep 2>/dev/null   # some shells wrap grep in a function; use the real one

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$ROOT/claude-account"
# Work inside the repo rather than /tmp, which is wiped on reboot or power loss.
mkdir -p "$ROOT/.test-tmp"
T=$(mktemp -d "$ROOT/.test-tmp/run.XXXXXX")
export T

cleanup() {
    pkill -KILL -f "$T/fake-" 2>/dev/null
    rm -rf -- "$T"
}
trap cleanup EXIT

# ── fakes ────────────────────────────────────────────────────

mkdir -p "$T/bin" "$T/lib" "$T/trash"
# The fake app executable is a copy of bash, so /proc/PID/exe points into $T/lib.
cp "$(command -v bash)" "$T/lib/claude-desktop"

cat >"$T/fake-main.sh" <<'EOF'
# Fake Claude Desktop main process: records which account it opened with.
readlink "$XDG_CONFIG_HOME/Claude" >>"$T/launches" 2>/dev/null || echo REAL-FOLDER >>"$T/launches"
"$T/lib/claude-desktop" "$T/fake-helper.sh" --type=renderer &
if [[ -e $T/stubborn ]]; then trap '' TERM; else trap 'exit 0' TERM; fi
if [[ -n ${FAKE_RUN:-} ]]; then
    bash -c "$FAKE_RUN" &
fi
while :; do sleep 0.1; done
EOF

cat >"$T/fake-helper.sh" <<'EOF'
# Fake renderer: exits shortly after its parent.
while kill -0 "$PPID" 2>/dev/null; do sleep 0.1; done
EOF

cat >"$T/bin/claude-desktop" <<'EOF'
#!/usr/bin/env bash
exec "$T/lib/claude-desktop" "$T/fake-main.sh"
EOF

# Dialogs that need an answer take the next line of $T/answers: "<exit code>|<output>".
cat >"$T/bin/zenity" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$T/zenity.log"
case " $* " in
    *" --question "* | *" --entry "* | *" --list "*)
        line=$(head -n 1 "$T/answers" 2>/dev/null)
        sed -i 1d "$T/answers" 2>/dev/null
        [[ -n ${line#*|} ]] && printf '%s\n' "${line#*|}"
        exit "${line%%|*}"
        ;;
esac
exit 0
EOF

cat >"$T/bin/notify-send" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$T/notify.log"
EOF

cat >"$T/bin/gio" <<'EOF'
#!/usr/bin/env bash
# fake "gio trash -- PATH"
[[ $1 == trash ]] || exit 1
mv -- "${@: -1}" "$T/trash/"
EOF

chmod +x "$T/bin/"*

# ── helpers ──────────────────────────────────────────────────

CFG="$T/config"   # XDG_CONFIG_HOME used by the script under test
OUT=""            # output of the last ca call
RC=0              # exit code of the last ca call

run_env() {
    env -u CLAUDE_ACCOUNT_DETACHED -u CLAUDE_ACCOUNT_UI \
        XDG_CONFIG_HOME="$CFG" \
        CLAUDE_ACCOUNT_EXE="$T/lib/claude-desktop" \
        CLAUDE_ACCOUNT_QUIT_TIMEOUT=2 \
        PATH="$T/bin:$PATH" \
        WAYLAND_DISPLAY="" DISPLAY="" \
        "$@"
}

ca() {
    OUT=$(run_env "$SCRIPT" "$@" </dev/null 2>&1)
    RC=$?
}

fake_main_pids() { pgrep -f "^$T/lib/claude-desktop $T/fake-main.sh" || true; }
running() { [[ -n $(fake_main_pids) ]]; }
launch_count() { if [[ -f $T/launches ]]; then wc -l <"$T/launches"; else echo 0; fi; }
last_launch() { tail -n 1 "$T/launches" 2>/dev/null; }
# Claude is started in the background, so these are polled with wait_for.
last_launch_is() { [[ $(last_launch) == "$CFG/Claude-accounts/$1" ]]; }
launched_more_than() { [[ $(launch_count) -gt $1 ]]; }

start_fake() { # extra env assignments may be passed, e.g. FAKE_RUN=...
    run_env "$@" setsid -f "$T/bin/claude-desktop" </dev/null >/dev/null 2>&1
    wait_for running
}

stop_fake() {
    pkill -KILL -f "$T/fake-" 2>/dev/null
    wait_for not_running
}
not_running() { ! running; }

wait_for() {
    local i
    for ((i = 0; i < 50; i++)); do
        "$@" && return 0
        sleep 0.2
    done
    return 1
}

link_target() { readlink "$CFG/Claude" 2>/dev/null || echo "-"; }

PASS=0 FAIL=0
check() {
    local desc=$1
    shift
    if "$@"; then
        PASS=$((PASS + 1))
        printf 'ok    %s\n' "$desc"
    else
        FAIL=$((FAIL + 1))
        printf 'FAIL  %s\n' "$desc"
        printf '      rc=%s out=%s\n' "$RC" "$OUT"
    fi
}
ok() { [[ $RC == 0 ]]; }
failed() { [[ $RC != 0 ]]; }
out_has() { [[ $OUT == *"$1"* ]]; }
is_link_to() { [[ $(link_target) == "$CFG/Claude-accounts/$1" ]]; }

# ── scenario: first run, CLI ─────────────────────────────────

mkdir -p "$CFG/Claude"
echo "main-data" >"$CFG/Claude/marker"
start_fake

ca list
check "list explains the not-yet-initialised state" out_has "尚未初始化"

ca add work --yes
check "add refuses before the current account has a name" failed
check "…and leaves the real folder alone" test -d "$CFG/Claude" -a ! -L "$CFG/Claude"

ca add ../evil --as main --yes
check "add rejects path-like names" failed
check "…and changes nothing" test ! -e "$CFG/Claude-accounts/main"

ca switch main --yes
check "switch before any extra account is refused" failed

old_pid=$(fake_main_pids)
before=$(launch_count)
ca init main --yes
check "init succeeds" ok
check "init turns ~/.config/Claude into a link to main" is_link_to main
check "existing data is reachable through the link" grep -q main-data "$CFG/Claude/marker"
check "the old Claude process was closed" test ! -d "/proc/$old_pid"
check "Claude was started again because it had been running" wait_for launched_more_than "$before"
check "…and it opened the main account" wait_for last_launch_is main

ca add work --yes
check "add work succeeds" ok
check "link now points at work" is_link_to work
check "new account folder is private (700)" test "$(stat -c %a "$CFG/Claude-accounts/work")" = 700
check "Claude reopened in work" wait_for last_launch_is work
check "main data is untouched" grep -q main-data "$CFG/Claude-accounts/main/marker"

ca list
check "list marks the active account" out_has "* work"
check "list shows the other account" out_has "  main"

# Right after a switch: fails if Claude inherited and kept our lock.
ca switch main --yes
check "switching back immediately works (lock not leaked to Claude)" ok
check "link points at main" is_link_to main
check "Claude reopened in main" wait_for last_launch_is main

before=$(launch_count)
ca switch main --yes
check "switching to the active account is a no-op" out_has "已經是"
sleep 0.5
check "…and does not restart a running Claude" test "$(launch_count)" = "$before"

ca switch nope --yes
check "switching to an unknown account fails" failed
check "…and keeps the link" is_link_to main

ca remove main --yes
check "removing the active account is refused" failed

ca remove work --yes
check "remove moves the account to the trash" test -d "$T/trash/work" -a ! -e "$CFG/Claude-accounts/work"

stop_fake
ca add work2 --yes
check "add works while Claude is closed" ok
check "…and starts Claude in the new account" wait_for last_launch_is work2

# ── safety cases ─────────────────────────────────────────────

stop_fake
touch "$T/stubborn"
start_fake
ca switch main --yes
check "a Claude that ignores quit is not force-killed without asking" failed
check "…the link is unchanged" is_link_to work2
check "…and Claude is still running" running
rm -f "$T/stubborn"
stop_fake

exec 8>"$CFG/Claude-accounts/.lock"
flock 8
ca switch main --yes
check "a second switch while one is running is refused" out_has "另一個切換正在進行"
exec 8>&-

mv -T "$CFG/Claude" "$CFG/Claude.saved"
mkdir -p "$T/elsewhere"
ln -s "$T/elsewhere" "$CFG/Claude"
ca switch main --yes
check "a link not created by the tool is left alone" failed
check "…untouched" test "$(readlink "$CFG/Claude")" = "$T/elsewhere"
rm "$CFG/Claude"
mv -T "$CFG/Claude.saved" "$CFG/Claude"

# Started from inside Claude (e.g. its terminal): must continue detached,
# otherwise closing Claude would kill it halfway.
start_fake FAKE_RUN="'$SCRIPT' switch main --yes"
check "run from inside Claude: switch completes in the background" wait_for is_link_to main
check "…Claude came back in main" wait_for last_launch_is main
check "…and the log notes the detach" grep -q "continuing detached" "$CFG/Claude-accounts/.switcher.log"
stop_fake

ca restore --yes
check "restore succeeds" ok
check "~/.config/Claude is a real folder again" test -d "$CFG/Claude" -a ! -L "$CFG/Claude"
check "…holding the account that was active" grep -q main-data "$CFG/Claude/marker"
check "other accounts stay in the store" test -d "$CFG/Claude-accounts/work2"

# ── scenario: first run through the GUI ──────────────────────

CFG="$T/config-gui"
mkdir -p "$CFG/Claude"
echo "gui-main" >"$CFG/Claude/marker"
start_fake

printf '1|\n' >"$T/answers"
ca gui
check "gui: closing the menu changes nothing" test -d "$CFG/Claude" -a ! -L "$CFG/Claude"

printf '%s\n' '0|add' '0|personal' '0|work' '0|' >"$T/answers"
ca gui
check "gui add: succeeds" ok
check "gui add: current account adopted under the given name" grep -q gui-main "$CFG/Claude-accounts/personal/marker"
check "gui add: link points at the new account" is_link_to work
check "gui add: Claude reopened in the new account" wait_for last_launch_is work
check "gui add: asked before closing Claude" grep -q -- "--question" "$T/zenity.log"
check "gui add: told the user to log in" grep -q "登入" "$T/zenity.log"

printf '%s\n' '0|switch:personal' '0|' >"$T/answers"
ca gui
check "gui switch: link points at personal" is_link_to personal
check "gui switch: notification sent" grep -q personal "$T/notify.log"

printf '%s\n' '0|switch:work' '1|' >"$T/answers"
ca gui
check "gui switch: cancelling the close question changes nothing" is_link_to personal

printf '%s\n' '0|add' '0|bad/name' >"$T/answers"
ca gui
check "gui add: invalid name rejected" failed
check "gui add: …nothing changed" is_link_to personal
check "gui add: only one error dialog, no 'unexpected error'" test "$(grep -c "未預期" "$T/zenity.log")" = 0

printf '%s\n' '0|add' '1|' >"$T/answers"
ca gui
check "gui add: cancelling the name dialog exits quietly" ok
stop_fake

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[[ $FAIL == 0 ]]
