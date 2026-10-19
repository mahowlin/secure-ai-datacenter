# Optional endpoint and OSPF transit catalog fragments

The compact default needs neither a VMM endpoint domain nor an OSPF L3Out.
`catalog-fragments.yaml` preserves the independent endpoint/transit example.
Select and adapt its named entries under `spec.endpointNetworks` and
`spec.fabricNetworks` in your maintained network catalog only when those
integrations are required. Review the ACI tenant identity, existing domain/AAEP,
leaf ports, routing policy and addresses before rendering. Do not paste both old
and replacement tenant identities into the same catalog.

These fragments are not loaded by the default resolver root and are not a
substitute for the site's management/bootstrap network or upstream routing.
