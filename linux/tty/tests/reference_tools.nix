# Existing Apps composition consumed only by B's controlled integration test.
# No credentials, source records, floating refs or new product service.
let
  apps = builtins.getFlake "github:roccho-dev/apps/cbaed345182c19bcc7f0a244e5cc633dce415632";
  system = "x86_64-linux";
  pkgs = import apps.inputs.nixpkgs { inherit system; };
in pkgs.runCommand "meltype-reference-input-test-tools" { } ''
  mkdir -p "$out/bin"
  ln -s ${apps.apps.${system}.dev.program} "$out/bin/voice-ui-dev"
  ln -s ${apps.packages.${system}.voice-ui-reference-proposer}/propose.mjs "$out/propose.mjs"
  ln -s ${pkgs.git}/bin/git "$out/bin/git"
''
