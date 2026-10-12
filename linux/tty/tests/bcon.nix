# Test-only assembly. Reuse the exact existing bcon package; no resources write.
{ packagePath }:
let
  resources = builtins.fetchGit {
    url = "https://github.com/roccho-dev/resources.git";
    rev = "cfe7483db1658d299965c608a07f17f973fd79f7";
  };
  pkgs = import (resources + "/verification/pkgs.nix");
  bcon = pkgs.callPackage (resources + "/nixos/bcon.nix") { };
  ops = builtins.fetchGit {
    url = "https://github.com/roccho-org/ops.git";
    rev = "9e6603324e5d7691dd06675c3267e887ce1e8915";
  };
  viewer = pkgs.callPackage (ops + "/packages/cdp-tty/default.nix") { };
  page = pkgs.writeText "meltype-browser-proof.html" ''
    <!doctype html><meta charset="utf-8"><title>Meltype Direct TTY proof</title>
    <style>body{background:rgb(215,225,240);font:28px sans-serif}textarea{font:28px sans-serif;width:600px;height:120px}</style>
    <h1>Existing browser target</h1><textarea id="editor" autofocus></textarea>
  '';
  cjk = pkgs.noto-fonts-cjk-sans.override { static = true; };
  python = pkgs.python3.withPackages (p: [ p.pillow p.websocket-client ]);
  product = pkgs.stdenvNoCC.mkDerivation {
    pname = "meltype-tty-trial";
    version = "0";
    src = builtins.path { path = builtins.toPath packagePath; name = "same-build-meltype"; };
    nativeBuildInputs = [ pkgs.autoPatchelfHook pkgs.makeWrapper ];
    buildInputs = [ pkgs.stdenv.cc.cc.lib pkgs.icu pkgs.zlib pkgs.openssl ];
    runtimeDependencies = [ pkgs.icu ];
    dontBuild = true;
    installPhase = ''
      mkdir -p "$out/lib/meltype" "$out/bin"
      cp -r . "$out/lib/meltype/"
      chmod +x "$out/lib/meltype/mozc/meltype_mozc_helper"
      font=${cjk}/share/fonts/opentype/noto-cjk/NotoSansCJK-Regular.ttc
      test -r "$font" || { echo "declared CJK font is missing: $font" >&2; exit 1; }
      makeWrapper ${python}/bin/python3 "$out/bin/meltype-tty" \
        --add-flags "$out/lib/meltype/tty/meltype_tty.py" \
        --add-flags "--native $out/lib/meltype/libMeltypeNative.so" \
        --add-flags "--mozc $out/lib/meltype/mozc/meltype_mozc_helper" \
        --add-flags "--font $font" \
        --prefix LD_LIBRARY_PATH : "${pkgs.lib.makeLibraryPath [ pkgs.icu pkgs.zlib pkgs.openssl pkgs.stdenv.cc.cc.lib ]}"
    '';
  };
  ready = pkgs.writeShellScript "meltype-test-drm-ready" ''
    for i in $(seq 1 100); do
      for card in /dev/dri/card*; do
        test ! -c "$card" || exit 0
      done
      sleep .1
    done
    exit 1
  '';
in pkgs.testers.runNixOSTest {
  name = "meltype-direct-tty-bcon";
  globalTimeout = 240;
  extraPythonPackages = p: [ p.pillow ];
  nodes.machine = { ... }: {
    virtualisation.memorySize = 3072;
    virtualisation.cores = 2;
    hardware.graphics.enable = true;
    services.dbus.enable = true;
    fonts.packages = [ pkgs.noto-fonts cjk ];
    environment.systemPackages = [ bcon product viewer python pkgs.kbd pkgs.procps ];
    environment.etc."bcon/config.toml".text = "[terminal]\nime = false\n";
    users.groups.browser-proof = { };
    users.users.browser-proof = { isSystemUser = true; group = "browser-proof"; };
    # Test-owned browser exists before the viewer attaches. No user auth/profile.
    systemd.services.meltype-test-browser = {
      wantedBy = [ "multi-user.target" ];
      environment.HOME = "/run/meltype-test-browser";
      serviceConfig = {
        User = "browser-proof";
        RuntimeDirectory = "meltype-test-browser";
        ExecStart = "${pkgs.chromium}/bin/chromium --headless --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir=/run/meltype-test-browser/profile --window-size=800,600 file://${page}";
        Restart = "no";
      };
    };
    services.logind.settings.Login = { NAutoVTs = 1; ReserveVT = 1; };
    systemd.services."getty@tty2".enable = false;
    systemd.services.meltype-test-console = {
      wantedBy = [ "multi-user.target" ];
      after = [ "systemd-user-sessions.service" ];
      path = [ pkgs.coreutils ];
      environment = {
        HOME = "/root";
        XDG_RUNTIME_DIR = "/run/meltype-test-console";
        TERM = "xterm-256color";
        LIBGL_ALWAYS_SOFTWARE = "1";
        RUST_LOG = "info";
      };
      serviceConfig = {
        ExecStartPre = [ "${ready}" "${pkgs.kbd}/bin/chvt 2" ];
        ExecStart = "${bcon}/bin/bcon";
        StandardInput = "tty";
        StandardOutput = "tty";
        StandardError = "journal";
        TTYPath = "/dev/tty2";
        RuntimeDirectory = "meltype-test-console";
        TimeoutStopSec = "5s";
      };
    };
  };
  testScript = builtins.readFile ./bcon.py;
}
