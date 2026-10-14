"""One module per feature, each adding its routes to the app.

web/app.py was a single 2,700-line create_app(). The routes moved out of it
verbatim; what stayed is the setup and the request plumbing they share,
handed to each register() as one namespace.
"""
