{
  description = "ai-query-agent - NL2SQL MCP Gateway (Nix + systemd, no Docker)";

  nixConfig = {
    bash-prompt = "[ai-query-agent]$ ";
    extra-substituters = [ "https://cache.nixos.org" ];
    extra-trusted-public-keys = [ "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY=" ];
  };

  inputs = {
    nixpkgs.url = "https://flakehub.com/f/DeterminateSystems/nixpkgs-weekly/0.1";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # Nix 只提供解释器 + pip；业务依赖由两份 lock 装入 venv
      pythonEnv = pkgs.python311.withPackages (ps: with ps; [ pip virtualenv ]);

      # venv 中的 C++ wheel（onnxruntime 等）在运行期需要 libstdc++；
      # 并入 python 输出后，`nix-python` out-link 同时充当它的 GC root，
      # 运行脚本可直接用 $AI_QUERY_STATE/nix-python/lib 而不必写死 store 路径。
      pythonRuntime = pkgs.symlinkJoin {
        name = "ai-query-agent-python";
        paths = [ pythonEnv pkgs.stdenv.cc.cc.lib ];
      };

      # 便捷入口：从调用者当前目录执行仓库内脚本（systemd 才是主路径）
      mkApp = name: extra: {
        type = "app";
        program = "${pkgs.writeShellScript "ai-query-${name}" ''
          set -euo pipefail
          ${extra}
          exec ./scripts/run-${name}.sh
        ''}";
      };
    in {
      packages.${system} = {
        python = pythonRuntime;
        default = pythonRuntime;
      };

      apps.${system} = {
        agent = mkApp "agent" "";
        proxy = mkApp "proxy" "";
        mock-upstream = mkApp "mock-upstream" "";
        test = {
          type = "app";
          program = "${pkgs.writeShellScript "ai-query-test" ''
            set -euo pipefail
            exec ./scripts/run-tests.sh
          ''}";
        };
      };

      devShells.${system}.default = pkgs.mkShell {
        name = "ai-query-agent-dev";
        buildInputs = [
          pythonEnv
          pkgs.curl
          pkgs.gcc
          pkgs.stdenv.cc.cc.lib
          pkgs.glibc
        ];
        shellHook = ''
          export PYTHONPATH="$(pwd):''${PYTHONPATH:-}"
          export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:${pkgs.glibc}/lib:''${LD_LIBRARY_PATH:-}"
          echo "ai-query-agent dev shell — Python $(python --version 2>&1 | cut -d' ' -f2)"
          echo "  首次准备依赖:  bash scripts/setup-venv.sh"
          echo "  运行服务:      nix run .#agent"
          echo "  运行测试:      nix run .#test"
        '';
      };
    };
}
