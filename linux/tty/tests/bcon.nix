# Test-only assembly. Reuse the exact existing bcon package; no resources write.
{ packagePath }:
let
  resources = builtins.fetchGit {
    url = "https://github.com/roccho-dev/resources.git";
    rev = "cfe7483db1658d299965c608a07f17f973fd79f7";
  };
  pkgs = import (resources + "/verification/pkgs.nix");
  bcon = pkgs.callPackage (resources + "/nixos/bcon.nix") { };
  python = pkgs.python3.withPackages (p: [ p.pillow ]);
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
      font=$(find ${pkgs.noto-fonts-cjk-sans} -name NotoSansCJK-Regular.ttc -print -quit)
      test -n "$font"
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
  globalTimeout = 180;
  extraPythonPackages = p: [ p.pillow ];
  nodes.machine = { ... }: {
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    hardware.graphics.enable = true;
    services.dbus.enable = true;
    fonts.packages = [ pkgs.noto-fonts pkgs.noto-fonts-cjk-sans ];
    environment.systemPackages = [ bcon product python pkgs.kbd pkgs.procps ];
    environment.etc."bcon/config.toml".text = "[terminal]\nime = false\n";
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
