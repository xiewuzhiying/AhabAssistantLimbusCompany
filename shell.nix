{ pkgs ? import <nixpkgs> {} }:

let
  env = import ./nix { inherit pkgs; };
in
env.devShell
