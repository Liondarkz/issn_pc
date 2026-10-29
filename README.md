# issn_pc: inFAMOUS Second Son nativo no PC

Port nativo de **inFAMOUS Second Son** (PS4) para **Windows** (Vulkan). Builds de teste: o jogo é
jogável, mas espere bugs. Os shaders são preparados na primeira abertura (alguns minutos, uma
vez).

> **Nenhum arquivo do jogo vem neste projeto.** Você precisa do dump do seu próprio jogo, feito
> da sua cópia no seu PS4: veja [docs/issn/DUMP.md](docs/issn/DUMP.md).
> Projeto sem relação com a Sony Interactive Entertainment ou a Sucker Punch Productions.
> inFAMOUS é marca da Sony Interactive Entertainment.

💬 **Discord:** https://discord.gg/y82HQ5uTQg (novidades, ajuda e builds de teste / news, help and test builds)

[English below](#english)

## Download

Em [Releases](../../releases), baixe o `issn-windows.zip`. Extraia em qualquer pasta (fora do
zip) e abra `inFAMOUS Second Son.exe`. Escolha a pasta do jogo (a que tem o `eboot.bin`) e clique
em PLAY. Se o Windows mostrar "O Windows protegeu o computador": Mais informações → Executar
assim mesmo (o programa não é assinado).

Para atualizar, extraia o zip novo por cima da pasta antiga: saves e configurações ficam.

**Requisitos:** Windows 10 (1903+) ou 11 de 64 bits, processador com AVX, placa de vídeo com
Vulkan 1.3 e driver atualizado. Versões do jogo: CUSA00223 (EUA) e CUSA00004 (Europa).

## O que tem

- **Motor gráfico do axylpup** para o Second Son (shadPS4): num Ryzen 7 3700X + RX 9060 XT,
  90–105 FPS parado na cidade.
- **Anti-aliasing:** o do próprio jogo, **FSR 4** (já incluído; Radeon RX 7000 / 9000) ou
  **DLSS** (NVIDIA RTX; os arquivos da NVIDIA não vêm junto: baixe o Streamline SDK 2.14.16 e
  copie `sl.interposer.dll`, `sl.common.dll`, `sl.dlss.dll`, `sl.pcl.dll` e `nvngx_dlss.dll` de
  `bin\x64` para `bin\dlss`; o launcher abre essa pasta para você).
- **Shaders preparados na primeira abertura:** o pacote traz uma semente (a lista dos shaders do
  jogo e das configurações deles, sem nenhum código do jogo) e a primeira abertura monta os
  shaders a partir dos arquivos do seu próprio jogo. Os de efeitos vêm prontos em qualquer placa;
  os outros em placas como a do testador (AMD recente). O que ainda for novo compila em segundo
  plano, sem travar o jogo.
- **Resolução interna** (83% a 50%) com o FSR 4 reconstruindo a imagem no tamanho da janela, e
  **Modo de desempenho** (os ajustes mais leves do próprio jogo, da 1.1 do axylpup).
- Limite de FPS e VSync; distância de detalhe original, Longa ou Muito longa; F12 tira print.
- **Mouse 1:1:** clique na janela do jogo e o mouse mira direto na câmera do jogo, sem a curva
  do analógico. Y ou F7 solta o mouse; `-` / `=` mudam a velocidade.
- **Teclado** (layout do axylpup): WASD anda, IJKL câmera, setas direcional, Espaço / Shift / C /
  F, Q / E, U / O, R / G touchpad, Enter. **Lata de spray:** segure Z para inclinar, toque Z ou
  segure X para chacoalhar, mire com o mouse. Os botões mudam no launcher.
- **Controles:** DualShock 4 e DualSense com touchpad e sensor de movimento; Xbox, 8BitDo e outros
  com o touchpad e a lata de spray emulados (L3 + R3 inclina, LB chacoalha, analógico direito mira).
- Launcher com "Adicionar à Steam"; aberto pela Steam, o controle continua funcionando.

## Compilar

No Windows, com o [MSYS2](https://www.msys2.org) (shell **CLANG64**): veja
[packaging/windows/README.md](packaging/windows/README.md). Depois das dependências:

```bash
git submodule update --init --recursive
bash build.sh
bash packaging/windows/package.sh   # dist/issn-windows.zip
```

## English

Native PC port of **inFAMOUS Second Son** (PS4) for **Windows** (Vulkan). Test builds: the game is
playable, but expect bugs. The first start prepares the shaders once (a few minutes).
**No game files are included**: you need a dump of your own copy (CUSA00223 US or CUSA00004 EU).

Download `issn-windows.zip` from [Releases](../../releases), extract it anywhere and open
`inFAMOUS Second Son.exe`, choose the game folder (with `eboot.bin`) and click PLAY. Windows 10
(1903+) / 11 64-bit, an AVX CPU and a Vulkan 1.3 GPU are needed. Features: axylpup's graphics core,
the game's AA, FSR 4 (included) or DLSS (bring NVIDIA's Streamline 2.14.16 files into `bin\dlss`),
shaders prepared on the first start from a shipped seed (shader list and settings, no game
code; new ones compile in the background), internal resolution with FSR 4 upscaling, performance
mode, frame rate limit, 1:1 mouse aim (click in the window; Y / F7 lets go), axylpup's keyboard layout,
DualShock 4 / DualSense touchpad and motion, touchpad and spray-can emulation for other pads, and
Add to Steam. Building: [packaging/windows/README.md](packaging/windows/README.md).

## Créditos / Credits

- Gráficos / graphics: o build do shadPS4 do **axylpup** para o Second Son
  ([axylpup/shadPS4](https://github.com/axylpup/shadPS4)), sobre o
  [shadPS4](https://github.com/shadps4-emu/shadPS4); trabalhos anteriores no inFAMOUS de
  **Ayoub** (shadPS4-AYOUB1080p) e **HayatoG** (iss_pc). Mouse 1:1, layout de teclado e
  sensor de movimento sintético: design do axylpup.
- Base do port / port base: [gow3_pc](https://github.com/FelipePrado0/gow3_pc) (Felipe Prado),
  que vem do [bloodborne_pc](https://github.com/Supermedo/bloodborne_pc) (deadinside28,
  Supermedo), com cuesta4 e a equipe do shadPS4. Documentação herdada: [docs/base](docs/base/README.md).
- FSR 4: AMD FidelityFX (MIT). Fonte do launcher: Bebas Neue (SIL OFL).

## Licença / License

GNU GPL v2 ou posterior ([LICENSE](LICENSE)). Componentes de terceiros mantêm suas licenças.
