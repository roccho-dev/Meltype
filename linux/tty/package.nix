# B-owned reusable closure for a pinned same-build Meltype-linux artifact.
# C may import this file from an immutable B revision; no duplicate recipe.
# No Fcitx5, IBus, SKK or default authorization of raw no-echo capture.
{ pkgs, packagePath }:
let
  cjk = pkgs.noto-fonts-cjk-sans.override { static = true; };
  python = pkgs.python3.withPackages (p: [ p.pillow ]);
in pkgs.stdenvNoCC.mkDerivation {
    pname = "meltype-tty-trial";
    version = "0";
    src = builtins.path { path = builtins.toPath packagePath; name = "same-build-meltype"; };
    nativeBuildInputs = [ pkgs.autoPatchelfHook pkgs.makeWrapper ];
    buildInputs = [ pkgs.stdenv.cc.cc.lib pkgs.icu pkgs.zlib pkgs.openssl ];
    runtimeDependencies = [ pkgs.icu ];
    dontBuild = true;
    installPhase = ''
      mkdir -p "$out/lib/meltype" "$out/bin"
      # The shared Linux ZIP also contains IBus/fcitx5 products. Select only
      # this entry's runtime; do not introduce addon dependencies into B.
      cp libMeltypeNative.so LICENSE THIRD-PARTY-NOTICES.md "$out/lib/meltype/"
      cp -r mozc tty "$out/lib/meltype/"
      chmod +x "$out/lib/meltype/mozc/meltype_mozc_helper"
      font=${cjk}/share/fonts/opentype/noto-cjk/NotoSansCJK-Regular.ttc
      test -r "$font" || exit 1
      makeWrapper ${python}/bin/python3 "$out/bin/meltype-tty" \
        --add-flags "$out/lib/meltype/tty/meltype_tty.py" \
        --add-flags "--native $out/lib/meltype/libMeltypeNative.so" \
        --add-flags "--mozc $out/lib/meltype/mozc/meltype_mozc_helper" \
        --add-flags "--font $font" \
        --prefix LD_LIBRARY_PATH : "${pkgs.lib.makeLibraryPath [ pkgs.icu pkgs.zlib pkgs.openssl pkgs.stdenv.cc.cc.lib ]}"
    '';
  }
