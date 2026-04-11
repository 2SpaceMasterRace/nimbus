{
  description = "Example Nix flake for an OSPSD-style Python workspace";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            git
            gh
            docker
            tmux
            zsh
            ripgrep
            fd
            jq
            python312
            uv
          ];

          shellHook = ''
            echo "Entered the example Nix dev shell"
            echo "Try: uv --version && python --version && git --version"
          '';
        };

        packages.default = pkgs.writeShellApplication {
          name = "show-tool-versions";
          runtimeInputs = with pkgs; [ git gh python312 uv ];
          text = ''
            echo "git: $(git --version)"
            echo "gh: $(gh --version | head -n 1)"
            echo "python: $(python --version 2>&1)"
            echo "uv: $(uv --version)"
          '';
        };
      });
}
