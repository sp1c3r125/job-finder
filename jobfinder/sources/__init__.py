from . import ashby, greenhouse, himalayas, lever, remoteok, wwr

ATS = {"greenhouse": greenhouse.fetch, "lever": lever.fetch, "ashby": ashby.fetch}       # fetch(http, company)
AGGREGATORS = {"himalayas": himalayas.fetch, "remoteok": remoteok.fetch, "wwr": wwr.fetch}  # fetch(http, cfg, log)

# Tie-break when the same job is posted on several sources at the same time: company ATS first.
PRIORITY = ["greenhouse", "lever", "ashby", "himalayas", "remoteok", "wwr", "research"]
