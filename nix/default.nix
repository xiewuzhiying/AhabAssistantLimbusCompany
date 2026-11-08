{ pkgs }:

let
  pythonPackages = pkgs.python3Packages;

  framelessWindow = pythonPackages.buildPythonPackage rec {
    pname = "pysidesix-frameless-window";
    version = "0.8.2";
    src = pythonPackages.fetchPypi {
      pname = "pysidesix_frameless_window";
      inherit version;
      hash = "sha256-PLv4r9OQ2p6kP8l9SjpCxTaq8I0kSnIrEU7/AFS3/ww=";
    };
    pyproject = true;
    build-system = with pythonPackages; [
      setuptools
      wheel
    ];
    dependencies = with pythonPackages; [
      pyside6
    ];
    doCheck = false;
  };

  fluentWidgets = pythonPackages.buildPythonPackage rec {
    pname = "pyside6-fluent-widgets";
    version = "1.11.3";
    src = pythonPackages.fetchPypi {
      pname = "pyside6_fluent_widgets";
      inherit version;
      hash = "sha256-v7hqEuDYjt8aG0Mnt56Edndxug0qKUxMsf4XDgJuvBY=";
    };
    pyproject = true;
    build-system = with pythonPackages; [
      setuptools
      wheel
    ];
    dependencies = with pythonPackages; [
      pyside6
      framelessWindow
      darkdetect
    ];
    doCheck = false;
  };

  playsound3 = pythonPackages.buildPythonPackage rec {
    pname = "playsound3";
    version = "2.4.0";
    src = pythonPackages.fetchPypi {
      inherit pname version;
      hash = "sha256-VTOSLe5AZ4qzXXUuPcUEdRlklgW9g1tAEuBXfmiQewQ=";
    };
    pyproject = true;
    build-system = [
      pythonPackages.hatchling
    ];
    dependencies = [
      pythonPackages.certifi
    ];
    doCheck = false;
  };

  adbutils = pythonPackages.buildPythonPackage rec {
    pname = "adbutils";
    version = "2.12.0";
    src = pythonPackages.fetchPypi {
      inherit pname version;
      hash = "sha256-NlOo85c1YgvEWxXuLnoA5QLJ8aJZRS4fsru6PqWdDmg=";
    };
    pyproject = true;
    build-system = with pythonPackages; [
      setuptools
      pbr
    ];
    dependencies = with pythonPackages; [
      requests
      deprecation
      retry2
      pillow
    ];
    doCheck = false;
  };

  pythonEnv = pkgs.python3.withPackages (ps: with ps; [
    # GUI
    pyside6
    fluentWidgets
    framelessWindow

    # Audio & Automation
    playsound3
    adbutils
    pyautogui
    pynput
    pyperclip
    xlib
    mss

    # Archives & Network
    py7zr
    requests
    packaging

    # Markdown & Config
    markdown-it-py
    mdit-py-plugins
    linkify-it-py
    ruamel-yaml

    # CV & ML & Logging
    numpy
    opencv4
    pillow
    psutil
    onnxruntime
    concurrent-log-handler
    colorlog
    rapidocr
    tzdata
    pydantic

    # Dev & Test
    pytest
    ruff
    watchdog
  ]);

  devShell = pkgs.mkShell {
    name = "aalc-dev-shell";

    packages = [
      pythonEnv
      pkgs.xdotool
      pkgs.wmctrl
      pkgs.gamescope
    ];

    shellHook = ''
      export PYTHONPATH="$PWD:$PYTHONPATH"
    '';
  };
in
{
  inherit
    framelessWindow
    fluentWidgets
    playsound3
    adbutils
    pythonEnv
    devShell;
}
