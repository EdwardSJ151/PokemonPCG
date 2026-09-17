# Pokémon PCG

## Clone and install

Clone this repository and enter its directory:

```bash
git clone https://github.com/<your-github-name>/PokemonPCG.git
cd PokemonPCG
```

Clone the upstream data and simulator repositories into the project root:

```bash
git clone https://github.com/smogon/damage-calc.git damage-calc
git clone https://github.com/pret/pokeemerald.git pokeemerald
git clone https://github.com/pret/pokeheartgold.git pokeheartgold
git clone https://github.com/pret/pokeplatinum.git pokeplatinum
git clone https://github.com/smogon/pokemon-showdown.git pokemon-showdown
```

Install the Python dependencies:

```bash
python -m pip install numpy pillow
```

Install npm dependencies for every JavaScript project:

```bash
(cd viewer && npm install)
(cd damage-calc && npm install)
(cd pokemon-showdown && npm install)
```

If a repository already exists, update it instead of cloning it again:

```bash
git -C damage-calc pull
git -C pokeemerald pull
git -C pokeheartgold pull
git -C pokeplatinum pull
git -C pokemon-showdown pull
```

## Generate extracted data

Run these commands from the project root:

```bash
python3 terrain_to_ascii.py emerald --all terrain_maps_emerald/ && \
python3 terrain_to_ascii.py heartgold --all terrain_maps_heartgold/ && \
python3 terrain_to_ascii.py platinum --all terrain_maps_platinum/
```

## Run the map viewer

Generate the viewer's data bundle, then start the development server:

```bash
python viewer/export_viewer_data.py all
cd viewer
npm run dev
```

Open the URL printed by Vite, normally <http://localhost:5173>.

To make a build:

```bash
npm run build
```