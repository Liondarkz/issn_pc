# SPDX-License-Identifier: GPL-2.0-or-later
"""inFAMOUS Second Son's trophy names, for players without a trophy key (scripts/trophies.py).

Written by hand from public trophy lists, not taken from the game's files:
- English names, descriptions, grades and order: PlayStation's own list as shown by PlatPursuit
  (NPWR05740_00); the order matches the icons of the game's trophy file (0 platinum cup, 2/3 good
  and evil Delsin, 14 open lock = "Everybody Out", 39 car = "Total CARnage", ...).
- Portuguese: PlayStation Blast (2015 article with the Brazilian texts); its typo "Estillhaçado"
  is corrected. The platinum's Portuguese name was not in it: "Aproveite seus poderes" is our own.
- Spanish: Xataka México's trophy guide (38 of them); the ten story trophies it left out
  (4-13) are our own translations.
Names that are our own may differ from the game's; the player's own key (Achievements page) gives
the game's texts instead.
"""

COMM_ID = 'NPWR05740_00'
TITLE_IDS = ('CUSA00004', 'CUSA00223')

# (grade, English name, English description, Portuguese name, Portuguese description,
#  Spanish name, Spanish description), by trophy id.
TROPHIES = [
    ('P', 'Enjoy Your Powers', 'Collect all trophies',
     'Aproveite seus poderes', 'Obtenha todos os troféus',
     'Disfruta tus poderes', 'Obtén todos los trofeos.'),
    ('S', 'Unstoppable', 'Finish the game on Expert difficulty',
     'Imbatível', 'Termine o jogo na dificuldade Especialista',
     'Imparable', 'Termina el juego en nivel Experto.'),
    ('B', 'Sacrifice', 'Risk your freedom to protect those you care about',
     'Sacrifício', 'Arrisque sua liberdade para proteger aqueles com quem se importa',
     'Sacrificio', 'Arriesga tu libertad por proteger a los que quieres.'),
    ('B', 'Ruthless', 'Look out for yourself, no matter the consequences',
     'Implacável', 'Proteja-se, não importam as consequências',
     'Despiadado', 'Mira solo por ti, pase lo que pase.'),
    ('B', 'Temperance', 'Practice self-control',
     'Moderação', 'Pratique o autocontrole',
     'Templanza', 'Practica el autocontrol.'),
    ('B', 'Malice', 'Get them before they get you',
     'Malícia', 'Pegue eles antes que te peguem',
     'Malicia', 'Atrápalos antes de que te atrapen.'),
    ('B', 'Courage', 'Stand up for the helpless',
     'Coragem', 'Levante-se pelos indefesos',
     'Valor', 'Defiende a los indefensos.'),
    ('B', 'Contempt', "Don't fear the weak",
     'Desobediência', 'Não tema os fracos',
     'Desprecio', 'No temas a los débiles.'),
    ('B', 'Mercy', 'Everyone deserves a second chance',
     'Misericórdia', 'Todos merecem uma segunda chance',
     'Piedad', 'Todos merecen una segunda oportunidad.'),
    ('B', 'Revenge', 'An eye for an eye, a tooth for a tooth',
     'Vingança', 'Olho por olho, dente por dente',
     'Venganza', 'Ojo por ojo, diente por diente.'),
    ('B', 'Justice', 'Expose the corruption of the powerful',
     'Justiça', 'Exponha a corrupção dos poderosos',
     'Justicia', 'Expón la corrupción de los poderosos.'),
    ('B', 'Dominance', 'Show the world that nobody can protect them',
     'Domínio', 'Mostre ao mundo que ninguém pode protegê-los',
     'Dominio', 'Muestra al mundo que nadie puede protegerlos.'),
    ('G', 'Reconciliation', 'Finish the story with Good Karma',
     'Reconciliação', 'Termine a história com carma do bem',
     'Reconciliación', 'Termina la historia con karma del bien.'),
    ('G', 'Rebellion', 'Finish the story with Evil Karma',
     'Rebelião', 'Termine a história com carma do mal',
     'Rebelión', 'Termina la historia con karma del mal.'),
    ('B', 'Everybody Out', 'Rescue 10 suspects from suspicion pens',
     'Todo mundo solto', 'Resgate 10 suspeitos das celas de suspeitos',
     'Todos fuera', 'Rescata a 10 sospechosos de las celdas.'),
    ('B', 'Spin-off', 'Beat down 10 sign twirlers',
     'Chega de cartazes', 'Dê porrada em 10 pessoas com placas',
     'Basta de publicidad', 'Derrota a 10 portadores de anuncios.'),
    ('B', "Everyone's a Critic", 'Take out 10 street musicians',
     'Todo mundo é crítico', 'Acabe com 10 músicos de rua',
     'Todos somos críticos', 'Elimina a 10 músicos callejeros.'),
    ('B', 'Just Say No', 'Bust 10 drug dealers',
     'Drogas, tô fora', 'Pegue 10 traficantes',
     'Simplemente di no', 'Detén a 10 narcotraficantes.'),
    ('B', 'Freedom of Speech', 'Disrupt 10 activist rallies',
     'Liberdade de expressão', 'Atrapalhe 10 manifestações ativistas',
     'Libertad de expresión', 'Interrumpe 10 reuniones de activistas.'),
    ('B', 'What Did You Call Me?', 'Kill 10 obnoxious Akuran gang members',
     'Do que você me chamou?', 'Mate 10 membros idiotas da gangue Akulan',
     '¿Qué me has llamado?', 'Mata a 10 molestos miembros de la banda akulana.'),
    ('B', 'Someone Your Own Size', 'Rescue 10 escaped conduits from angry mobs',
     'Alguém do seu tamanho', 'Resgate 10 condutores fugitivos de multidões enfurecidas',
     'Alguien de tu tamaño', 'Rescata a 10 conductores escapados de las turbas violentas.'),
    ('B', 'Walk It Off', 'Heal 10 wounded civilians',
     'Caminha que passa', 'Cure 10 civis feridos',
     'Ya estás bien', 'Cura a 10 civiles heridos.'),
    ('B', 'Kicking and Screaming', 'Finish off 10 wounded enemies or civilians',
     'Agonizando', 'Finalize 10 inimigos feridos',
     'Remátalos', 'Acaba con 10 enemigos o civiles heridos.'),
    ('S', 'True Hero', 'Achieve Level 5 Good Karma',
     'Herói Verdadeiro', 'Atinja carma do bem no nível 5',
     'Héroe de verdad', 'Alcanza el nivel 5 de karma del bien.'),
    ('S', 'Infamous', 'Achieve Level 5 Evil Karma',
     'Infame', 'Atinja carma do mal no nível 5',
     'Infame', 'Alcanza el nivel 5 de karma del mal.'),
    ('B', 'Bweep! Bweep! Bweep!', 'Collect half the Blast Shards',
     'Bipe! Bipe! Bipe!', 'Colete metade dos estilhaços de explosões',
     '¡Bip! ¡Bip! ¡Bip!', 'Obtén la mitad de los fragmentos de la explosión.'),
    ('S', 'Shardcore', 'Collect all the Blast Shards',
     'Estilhaçado', 'Colete todos os estilhaços de explosões',
     'Fragmentado', 'Reúne todos los fragmentos de la explosión.'),
    ('B', 'Nothing To See Here', 'Destroy half the D.U.P. security cameras',
     'Nada para ver aqui', 'Destrua metade das câmeras de segurança do DUP',
     'Se acabó la función', 'Destruye la mitad de las cámaras de seguridad del D.U.P.'),
    ('S', 'Blinded', 'Destroy all of the D.U.P. security cameras',
     'Cego', 'Destrua todas as câmeras de segurança do DUP',
     'Cegados', 'Destruye todas las cámaras de seguridad del D.U.P.'),
    ('S', 'Inside Track', 'Collect all the Informant Audio Logs',
     'Nos trilhos', 'Colete todos os registros de áudio de informantes',
     'Grabación interna', 'Reúne todos los diarios de audio del informante.'),
    ('S', 'Blown Cover', 'Defeat all the D.U.P. Secret Agents',
     'Disfarce descoberto', 'Derrote todos os agentes secretos do DUP',
     'Sin tapadera', 'Derrota a todos los agentes secretos del D.U.P.'),
    ('S', 'Big Brother', 'Destroy all the D.U.P. Hidden Cameras',
     'Big Brother', 'Destrua todas as câmeras secretas do DUP',
     'Gran hermano', 'Destruye todas las cámaras ocultas del D.U.P.'),
    ('S', 'Coloring Inside the Lines', 'Complete all the Stencil Art',
     'Pintando dentro da linha', 'Conclua todas as artes de estêncil',
     'No te salgas de los puntos', 'Completa todos los grafitis con plantilla.'),
    ('B', 'Prank Call', 'Complete a District Showdown',
     'Trote', 'Conclua um confronto no distrito',
     'Broma telefónica', 'Completa un Enfrentamiento de distrito.'),
    ('B', 'Hello Operator', 'Complete Showdowns in half of the districts',
     'Alô, operadora', 'Conclua confrontos em metade dos distritos',
     'Hola, operadora', 'Completa Enfrentamientos en la mitad de los distritos.'),
    ('S', 'Final Showdown', 'Complete all of the District Showdowns in the city',
     'Último confronto', 'Conclua os confrontos em todos os distritos da cidade',
     'Enfrentamiento final', 'Completa todos los Enfrentamientos de distrito de la ciudad.'),
    ('B', 'One Down', 'Clear the D.U.P. completely out of a district',
     'Menos um', 'Expulse o DUP completamente de um distrito',
     'Uno menos', 'Expulsa por completo al D.U.P. de un distrito.'),
    ('B', 'Home Field Advantage', 'Clear the D.U.P. out of half the districts in the city',
     'Jogando em casa', 'Expulse o DUP de metade dos distritos da cidade',
     'Jugar en casa', 'Expulsa al D.U.P. de la mitad de los distritos de la ciudad.'),
    ('S', 'Clean Sweep', 'Clear the D.U.P. out of the entire city',
     'Limpeza total', 'Expulse o DUP da cidade inteira',
     'Limpieza', 'Expulsa al D.U.P. de toda la ciudad.'),
    ('B', 'Total CARnage', 'Destroy 5 cars in 5 seconds',
     'Carnificina total', 'Destrua 5 carros em 5 segundos',
     'Autocarnicería total', 'Destruye 5 coches en 5 segundos.'),
    ('B', 'Drop Everything', 'Comet Drop over 170 meters',
     'Como um cometa', 'Execute o cometa por mais de 170 metros',
     'Realmente certero', 'Realiza un Cometa de más de 170 metros.'),
    ('B', 'Keep It Up', 'Stay in the air for more than 45 seconds',
     'Continue assim', 'Fique no ar por mais de 45 segundos',
     'Sigue así', 'Permanece en el aire más de 45 segundos.'),
    ('B', 'Put That Anywhere', 'Defeat 10 enemies by detonating ammo crates',
     'Coloque isso em qualquer lugar', 'Derrote 10 inimigos ao detonar caixas de munição',
     'Pon eso donde sea', 'Derrota a 10 enemigos detonando cajas de munición.'),
    ('B', 'Hat Trick', 'Defeat an enemy using three different power sources',
     'Truque de mestre', 'Derrote um inimigo usando três fontes de poder diferentes',
     'Truco ofensivo', 'Derrota a un enemigo utilizando tres fuentes de poder diferentes.'),
    ('B', 'Double Smoke Stack Attack',
     'Dash through two red air vents without touching the ground and then Comet Drop on an enemy',
     'Ataque duplo de fumaça em sequência',
     'Passe rápido por duas saídas de ar vermelhas sem tocar o chão e caia como um cometa no inimigo',
     'Ataque de chimenea doble',
     'Corre por dos conductos de ventilación rojos sin tocar el suelo y haz un Cometa sobre un enemigo.'),
    ('B', 'Cough Drop', 'Comet Drop on three coughing enemies',
     'Pastilha para tosse', 'Execute o cometa em 3 inimigos com tosse',
     'Caída antitós', 'Realiza un Cometa sobre 3 enemigos que tosen.'),
    ('B', 'Make Fetch Proud', 'Hit three weak points with Neon Beam in 6 seconds',
     'Dê orgulho a Fetch', 'Atinja 3 pontos fracos com feixe de neon em 6 segundos',
     'Que Fetch esté orgullosa', 'Alcanza 3 puntos débiles con Rayo de neón en 6 segundos.'),
    ('B', 'Excessive Force', 'Use a Karmic Streak attack to defeat exactly one enemy',
     'Força excessiva', 'Use um ataque de sequência cármica para derrotar exatamente um inimigo',
     'Fuerza excesiva', 'Usa un ataque de Racha kármica para derrotar exactamente a un enemigo.'),
]


def language_column(language):
    """0 English, 1 Portuguese, 2 Spanish from a PS4 language id (ISSN_LANGUAGE)."""
    try:
        value = int(language)
    except (TypeError, ValueError):
        value = 1
    return 1 if value in (7, 17) else 2 if value in (3, 20) else 0


def texts(language=1):
    """{id: {'grade', 'base', 'hidden', 'name', 'detail'}} like trophies.read_texts."""
    column = language_column(language)
    result = {}
    for trophy_id, (grade, *names) in enumerate(TROPHIES):
        result[trophy_id] = {'grade': grade, 'base': grade != 'P', 'hidden': False,
                             'name': names[2 * column], 'detail': names[2 * column + 1]}
    return result
