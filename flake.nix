{
  description = "AhabAssistantLimbusCompany (AALC) Nix development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      env = import ./nix { inherit pkgs; };
    in
    {
      devShells.${system}.default = env.devShell;
    };
}
