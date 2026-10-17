# B-only test assembly. No Chrome/cdp-tty and no resources mutation.
{ packagePath }:
let
  resources = builtins.fetchGit {
    url = "https://github.com/roccho-dev/resources.git";
    rev = "cfe7483db1658d299965c608a07f17f973fd79f7";
  };
  pkgs = import (resources + "/verification/pkgs.nix");
  bcon = pkgs.callPackage (resources + "/nixos/bcon.nix") { };
  cjk = pkgs.noto-fonts-cjk-sans.override { static = true; };
  python = pkgs.python3.withPackages (p: [ p.pillow ]);
  product = import ../package.nix { inherit pkgs packagePath; };
  ready = pkgs.writeShellScript "meltype-test-drm-ready" ''
    for i in $(seq 1 100); do
      for card in /dev/dri/card*; do test ! -c "$card" || exit 0; done
      sleep .1
    done
    exit 1
  '';
in pkgs.testers.runNixOSTest {
  name = "meltype-direct-input-bcon";
  globalTimeout = 360;
  extraPythonPackages = p: [ p.pillow ];
  nodes.machine = { ... }: {
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
    hardware.graphics.enable = true;
    services.dbus.enable = true;
    fonts.packages = [ pkgs.noto-fonts cjk ];
    environment.systemPackages = [ bcon product python pkgs.nano pkgs.kbd pkgs.procps ];
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
